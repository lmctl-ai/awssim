#!/usr/bin/env python3
"""Automatic AWS simulator releases retain 0.2 and derive patch from run number."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
match = re.fullmatch(r"(0\.2)\.(\d+)", current)
assert match, current
patch = int(match[2])

def derive(*args):
    return subprocess.check_output(["bash", ROOT / "scripts/derive-release-version.sh", *args], text=True).strip()

assert derive("--diagnostic") == current
assert derive("1") == f"0.2.{patch + 1}"
assert derive("19") == f"0.2.{patch + 19}"
assert derive("19") == f"0.2.{patch + 19}"

workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
assert "branches: [main]" in workflow
assert "tags: ['v*']" not in workflow
assert "release: ${{ steps.version.outputs.release }}" in workflow
assert "needs: [version, native, parity-ddb]" in workflow
assert "scripts/publish-release.sh" in workflow
assert "gh release create \"$tag\"" in (ROOT / "scripts/publish-release.sh").read_text(encoding="utf-8")
print("PASS awssim automatic version and green-main publisher contract")
