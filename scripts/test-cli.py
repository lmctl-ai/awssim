#!/usr/bin/env python3
"""CLI contract checks, for native and JVM launchers."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
command = sys.argv[1:] or [str(root / 'dist' / 'awssim')]

def run(args, code=0):
    p = subprocess.run(command + args, capture_output=True, text=True, timeout=30)
    assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
    return p.stdout

assert run(['--version']).strip() == 'awssim ' + (root / 'VERSION').read_text().strip() + ' (floci 2.3.0)'
assert '127.0.0.1' in run(['--help'])
for args in [[], ['bogus'], ['serve', '--port'], ['serve', '--port', '0'],
             ['serve', '--port', '65536'], ['serve', '--port', 'x'],
             ['serve', '--wat', 'x'], ['start', '--data-dir', ''],
             ['serve', '--port', '1234', '--port', '1235']]:
    assert json.loads(run(args, 2))['ok'] is False
print('CLI: 11 checks passed')
