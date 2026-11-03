#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$ROOT/parity-ddb.lock.json"

if [[ ! -f "$LOCK_FILE" ]]; then
  echo "error: missing $LOCK_FILE" >&2
  exit 1
fi

read -r REPO_URL LOCKED_COMMIT < <(python3 -c '
import json, sys
lock = json.load(open(sys.argv[1]))
print(lock["repository"], lock["commit"])
' "$LOCK_FILE")

SUITE_DIR="${PARITY_DDB_DIR:-}"
if [[ -z "$SUITE_DIR" ]]; then
  if [[ -d "/home/ubuntu/cloud/paritysuite-ddb/.git" ]] && \
     [[ "$(git -C /home/ubuntu/cloud/paritysuite-ddb rev-parse HEAD 2>/dev/null || true)" == "$LOCKED_COMMIT" ]]; then
    SUITE_DIR="/home/ubuntu/cloud/paritysuite-ddb"
  else
    SUITE_DIR="$ROOT/.cache/paritysuite-ddb"
  fi
fi

if [[ ! -d "$SUITE_DIR/.git" ]]; then
  mkdir -p "$(dirname "$SUITE_DIR")"
  git clone --quiet "$REPO_URL" "$SUITE_DIR"
fi

CURRENT_COMMIT="$(git -C "$SUITE_DIR" rev-parse HEAD)"
if [[ "$CURRENT_COMMIT" != "$LOCKED_COMMIT" ]]; then
  git -C "$SUITE_DIR" fetch --quiet origin "$LOCKED_COMMIT"
  git -C "$SUITE_DIR" checkout --quiet "$LOCKED_COMMIT"
  CURRENT_COMMIT="$(git -C "$SUITE_DIR" rev-parse HEAD)"
fi

if [[ "$CURRENT_COMMIT" != "$LOCKED_COMMIT" ]]; then
  echo "error: paritysuite-ddb commit mismatch: got $CURRENT_COMMIT, expected $LOCKED_COMMIT" >&2
  exit 1
fi

if [[ ! -d "$SUITE_DIR/node_modules" ]]; then
  (cd "$SUITE_DIR" && (npm ci --no-audit --no-fund --silent || npm install --no-audit --no-fund --silent))
fi

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
REPORT_JSON="$(mktemp /tmp/awssim-parity-ddb-XXXXXX.json)"
SERVER_LOG="$(mktemp /tmp/awssim-parity-ddb-server-XXXXXX.log)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$REPORT_JSON" "$SERVER_LOG"
}
trap cleanup EXIT

if [[ -n "${AWSSIM_BIN:-}" ]]; then
  "$AWSSIM_BIN" serve --port "$PORT" --auth open >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
elif [[ -x "$ROOT/dist/awssim" ]]; then
  "$ROOT/dist/awssim" serve --port "$PORT" --auth open >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
elif [[ -f "$ROOT/dist/quarkus-app/quarkus-run.jar" ]]; then
  java -jar "$ROOT/dist/quarkus-app/quarkus-run.jar" serve --port "$PORT" --auth open >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
else
  echo "error: no built awssim binary or JVM jar found under $ROOT/dist" >&2
  exit 1
fi

python3 - "$PORT" "$SERVER_LOG" <<'PY'
import sys, time, urllib.request

port = int(sys.argv[1])
log_path = sys.argv[2]
url = f"http://127.0.0.1:{port}/_floci/health"
deadline = time.time() + 30.0
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        time.sleep(0.2)
print("error: awssim failed to become healthy within 30s", file=sys.stderr)
try:
    with open(log_path) as f:
        print(f.read(), file=sys.stderr)
except Exception:
    pass
sys.exit(1)
PY

echo "Running DynamoDB Parity Suite ($LOCKED_COMMIT) against http://127.0.0.1:$PORT ..."
(
  cd "$SUITE_DIR"
  DYNAMODB_ENDPOINT="http://127.0.0.1:$PORT" npx vitest run tests/tier1 tests/tier2 tests/tier3 --reporter=json --outputFile="$REPORT_JSON" >/dev/null 2>&1 || true
)

python3 - "$LOCK_FILE" "$REPORT_JSON" <<'PY'
import json, sys

lock = json.load(open(sys.argv[1]))
report = json.load(open(sys.argv[2]))

passing_t3_files = set(lock.get("passingTier3Files", []))
known_t1 = set(lock.get("knownFailingTier1", []))
known_t2 = set(lock.get("knownFailingTier2", []))
known_t3 = set(lock.get("knownFailingTier3", []))

stats = {
    "tier1": {"pass": 0, "fail": 0, "skip": 0},
    "tier2": {"pass": 0, "fail": 0, "skip": 0},
    "tier3": {"pass": 0, "fail": 0, "skip": 0},
}
unexpected_failures = []
failing_protected_t3_files = set()

for suite in report.get("testResults", []):
    name = suite["name"]
    idx = name.find("tests/tier")
    rel = name[idx:] if idx >= 0 else name
    tier = "tier1" if "/tier1/" in name else ("tier2" if "/tier2/" in name else "tier3")
    for a in suite.get("assertionResults", []):
        st = a["status"]
        if st == "passed":
            stats[tier]["pass"] += 1
        elif st == "failed":
            stats[tier]["fail"] += 1
            key = f"{rel}::{a['fullName']}"
            if tier == "tier1" and key not in known_t1:
                unexpected_failures.append(key)
            elif tier == "tier2" and key not in known_t2:
                unexpected_failures.append(key)
            elif tier == "tier3":
                if rel in passing_t3_files:
                    failing_protected_t3_files.add(rel)
                if key not in known_t3:
                    unexpected_failures.append(key)
        else:
            stats[tier]["skip"] += 1

total_pass = sum(s["pass"] for s in stats.values())
total_fail = sum(s["fail"] for s in stats.values())
total_skip = sum(s["skip"] for s in stats.values())
total_all = total_pass + total_fail + total_skip

for t in ("tier1", "tier2", "tier3"):
    s = stats[t]
    tot = s["pass"] + s["fail"] + s["skip"]
    div = (s["fail"] / tot * 100.0) if tot else 0.0
    print(f"  {t}: pass={s['pass']}, fail={s['fail']}, skip={s['skip']}, total={tot}, divergence={div:.2f}%")
overall_div = (total_fail / total_all * 100.0) if total_all else 0.0
print(f"  OVERALL: pass={total_pass}, fail={total_fail}, skip={total_skip}, total={total_all}, divergence={overall_div:.2f}%")

if stats["tier1"]["pass"] == 0 or stats["tier2"]["pass"] == 0:
    print("error: tier1 or tier2 did not execute any passing tests", file=sys.stderr)
    sys.exit(1)

if failing_protected_t3_files:
    print("error: regressions in passingTier3Files:", file=sys.stderr)
    for f in sorted(failing_protected_t3_files):
        print(f"  - {f}", file=sys.stderr)

if unexpected_failures:
    print(f"error: {len(unexpected_failures)} unexpected test failure(s) not in parity-ddb.lock.json:", file=sys.stderr)
    for u in sorted(unexpected_failures):
        print(f"  - {u}", file=sys.stderr)
    sys.exit(1)

print("DynamoDB Parity Suite check PASSED (0 unexpected failures).")
PY
