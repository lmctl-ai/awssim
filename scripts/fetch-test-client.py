#!/usr/bin/env python3
"""Fetch the optional, pinned lmaws SDK acceptance client (never used at runtime)."""
import hashlib
import os
from pathlib import Path
import platform
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HASHES = {
    'linux-arm64': '1fe130e3f849d03a91b6b8a04d5626b5658fa2ccfface08c758b486b39630d02',
    'linux-x64': '92f6db93eef43f5bbbff202edd8247a3bd3e69a68cc4f7a51271ac8488601635',
    'macos-arm64': 'b79fdebce5abb576b8a274b2604b686a5cf0ef83759ddbf5bc061033ab1a2be8',
    'macos-x64': 'ca7863e3bcd866344dfbd6bf7154b4e171ce3b8c2db27cafe38e9360c5856565',
}
system = {'Linux': 'linux', 'Darwin': 'macos'}[platform.system()]
arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}[platform.machine()]
name = f'lmaws-{system}-{arch}'
path = ROOT / '.cache' / 'lmaws' / name
path.parent.mkdir(parents=True, exist_ok=True)
if not path.exists():
    with urllib.request.urlopen(f'https://github.com/lmctl-ai/lmaws/releases/download/v0.1.27/{name}', timeout=120) as response:
        path.write_bytes(response.read())
if hashlib.sha256(path.read_bytes()).hexdigest() != HASHES[f'{system}-{arch}']:
    raise SystemExit('lmaws checksum mismatch')
path.chmod(0o755)
if os.environ.get('GITHUB_ENV'):
    with open(os.environ['GITHUB_ENV'], 'a') as env:
        env.write(f'LMAWS_BINARY={path}\n')
print(path)
