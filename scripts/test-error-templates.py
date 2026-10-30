#!/usr/bin/env python3
"""Compile the small overlay renderer test using build-resolved Jackson jars."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
jars = list((Path.home() / '.m2/repository/com/fasterxml/jackson/core').rglob('*.jar'))
assert jars, 'Run scripts/build.py first to resolve Jackson'
classpath = os.pathsep.join(map(str, jars))
with tempfile.TemporaryDirectory(prefix='awssim-error-templates-') as tmp:
    subprocess.run(['javac', '-cp', classpath, '-d', tmp,
                    str(root / 'src/main/java/ai/lmctl/awssim/ErrorTemplates.java'),
                    str(root / 'tests/ErrorTemplatesTest.java')], check=True)
    subprocess.run(['java', '-cp', tmp + os.pathsep + classpath,
                    'ai.lmctl.awssim.ErrorTemplatesTest'], check=True)
