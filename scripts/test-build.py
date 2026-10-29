#!/usr/bin/env python3
"""Build must reject unverified source before invoking Maven or accessing the network."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='awssim-build-test-') as temp:
    root = Path(temp)
    (root / 'scripts').mkdir()
    shutil.copy2(ROOT / 'scripts/build.py', root / 'scripts/build.py')
    shutil.copy2(ROOT / 'upstream.lock.json', root / 'upstream.lock.json')
    command = [sys.executable, str(root / 'scripts/build.py'), '--offline']
    missing = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert missing.returncode != 0 and 'requires cached' in missing.stderr, missing
    (root / '.cache/floci.tar.gz').write_bytes(b'not the pinned Floci source')
    tampered = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert tampered.returncode != 0 and 'checksum mismatch' in tampered.stderr, tampered
    assert not (root / 'build').exists()
print('Build integrity: missing offline cache and tampered source rejected')
