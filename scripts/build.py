#!/usr/bin/env python3
"""Verify pinned Floci source, apply our CLI overlay, build on this host."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / 'upstream.lock.json').read_text())
cache = ROOT / '.cache'
cache.mkdir(exist_ok=True)
archive = cache / 'floci.tar.gz'
if not archive.exists():
    if '--offline' in sys.argv:
        sys.exit('Offline build requires cached .cache/floci.tar.gz')
    url = f"https://codeload.github.com/floci-io/floci/tar.gz/{lock['commit']}"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive.write_bytes(response.read())
if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
    sys.exit('Floci source checksum mismatch; remove .cache/floci.tar.gz and retry')
source = ROOT / 'build' / ('upstream-jvm' if '--jvm' in sys.argv else 'upstream')
stamp = source / '.awssim-source-sha256'
if source.exists() and (not stamp.exists() or stamp.read_text() != lock['sha256']):
    shutil.rmtree(source)
if not source.exists():
    source.parent.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(source.parent, filter='data')
    (source.parent / f"floci-{lock['commit']}").rename(source)
    stamp.write_text(lock['sha256'])
for path in (ROOT / 'src').rglob('*.java'):
    target = source / path.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text().replace('@VERSION@', (ROOT / 'VERSION').read_text().strip()).replace('@UPSTREAM@', lock['tag'])
    if not target.exists() or target.read_text() != content:
        target.write_text(content)
command = [shutil.which('mvn') or 'mvn', '-B', '-q', 'package', '-Dmaven.test.skip=true',
           '-Dcheckstyle.skip=true', '-Dmaven.compiler.useIncrementalCompilation=false',
           '-Dquarkus.package.main-class=ai.lmctl.awssim.Main']
if '--offline' in sys.argv:
    command.append('-o')
if '--jvm' not in sys.argv:
    command += ['-Dnative', '-Dquarkus.native.native-image-xmx=' + os.getenv('NATIVE_XMX', '7g'),
                '-Dquarkus.native.additional-build-args-append=-Ob,-march=compatibility']
subprocess.run(command, cwd=source, check=True)
if '--jvm' not in sys.argv:
    suffix = '.exe' if os.name == 'nt' else ''
    output = ROOT / 'dist' / ('awssim' + suffix)
    output.parent.mkdir(exist_ok=True)
    shutil.copy2(source / 'target' / ('floci-' + lock['tag'] + '-runner' + suffix), output)
    print(output)
