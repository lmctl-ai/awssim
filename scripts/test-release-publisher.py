#!/usr/bin/env python3
"""Publisher idempotence and repair controls use a local gh stub."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
version = subprocess.check_output(["bash", ROOT / "scripts/derive-release-version.sh", "19"], text=True).strip()
tag = "v" + version
source_sha = "0123456789abcdef0123456789abcdef01234567"

with tempfile.TemporaryDirectory(prefix="awssim-publisher-") as temp:
    root = Path(temp)
    assets = root / "assets"
    assets.mkdir()
    tools = root / "tools"
    tools.mkdir()
    remote = root / "public-release"
    calls = root / "gh-calls.txt"
    for name in ("awssim-linux-x64", "awssim-linux-arm64", "awssim-macos-x64", "awssim-macos-arm64", "awssim-windows-x64.exe"):
        path = assets / name
        if name == "awssim-linux-x64":
            path.write_text(f"#!/bin/sh\necho 'awssim {version} (floci 2.3.0)'\n", encoding="utf-8")
            path.chmod(0o755)
        else:
            path.write_bytes(b"foreign platform fixture")
    for name, body in (("LICENSE", b"license"), ("FLOCI-LICENSE", b"floci license"), ("upstream.lock.json", b"{}")):
        (assets / name).write_bytes(body)
    (assets / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(assets.iterdir())),
        encoding="utf-8",
    )
    stub = tools / "gh"
    stub.write_text("""#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
a=sys.argv[1:]; remote=Path(os.environ['REMOTE']); calls=Path(os.environ['CALLS'])
def record(): calls.open('a').write(' '.join(a)+'\\n')
if a[:2]==['release','view']:
 p=remote/'release.json'
 if not p.exists(): sys.exit(1)
 print(p.read_text())
elif a[:2]==['release','download']:
 record(); d=Path(a[a.index('--dir')+1]);d.mkdir(parents=True,exist_ok=True)
 for p in remote.iterdir():
  if p.name!='release.json':shutil.copyfile(p,d/p.name)
elif a[:2]==['release','create']:
 record();tag=a[2]; notes=Path(a[a.index('--notes-file')+1]); remote.mkdir(parents=True,exist_ok=True)
 for p in map(Path,a):
  if p.is_file() and p!=notes:shutil.copyfile(p,remote/p.name)
 (remote/'release.json').write_text(json.dumps({'tagName':tag,'isDraft':False,'isPrerelease':False,'body':notes.read_text()}))
elif a[:2]==['release','upload']:
 record();remote.mkdir(parents=True,exist_ok=True)
 for p in map(Path,a):
  if p.is_file():shutil.copyfile(p,remote/p.name)
else:sys.exit('unexpected gh command '+repr(a))
""", encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tools}:/usr/bin:/bin", GH_TOKEN="fixture", GITHUB_REPOSITORY="lmctl-ai/awssim",
               GITHUB_RUN_ID="19", GITHUB_SHA=source_sha, REMOTE=str(remote), CALLS=str(calls))

    def publish():
        return subprocess.run([ROOT / "scripts/publish-release.sh", tag, assets], env=env, capture_output=True, text=True)

    result = publish()
    assert result.returncode == 0, result.stderr
    meta = json.loads((remote / "release.json").read_text())
    assert meta["tagName"] == tag and not meta["isDraft"] and not meta["isPrerelease"]
    assert f"source-commit: {source_sha}" in meta["body"]
    result = publish()
    assert result.returncode == 0, result.stderr
    assert sum("create" in line for line in calls.read_text().splitlines()) == 1
    (remote / "awssim-linux-x64").write_text("corrupt", encoding="utf-8")
    result = publish()
    assert result.returncode == 0, result.stderr
    assert (remote / "awssim-linux-x64").read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert sum("create" in line for line in calls.read_text().splitlines()) == 1
    env["GITHUB_SHA"] = "fedcba9876543210fedcba9876543210fedcba98"
    result = publish()
    assert result.returncode != 0 and "source commit mismatch" in result.stderr, result.stderr

print("PASS awssim stable release create, immutable rerun, checksum repair and source collision refusal")
