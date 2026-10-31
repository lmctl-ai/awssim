#!/usr/bin/env python3
"""Fresh LMScript JSON templates -> signed wire calls and pinned lmaws SDK.

Uses botocore (Apache-2.0), already installed with the pinned AWS CLI in CI.
All files, credentials, endpoints and server state are isolated per invocation.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

ROOT = Path(__file__).resolve().parents[1]
COMMAND = sys.argv[1:] or [str(ROOT / 'dist/awssim')]
COMMAND[0] = str(Path(COMMAND[0]).resolve())
ENGINE = os.environ.get('LMSCRIPT_BIN') or shutil.which('lmscript')
CLIENT = os.environ.get('LMAWS_BINARY')
assert ENGINE and CLIENT, 'Set LMSCRIPT_BIN, LMAWS_BINARY and LMAWS_COMPILER (see README)'
ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(('AWS_', 'FLOCI_', 'QUARKUS_', 'SIM_', 'LMAWS_')) and 'proxy' not in k.lower()}
ENV.update(AWS_ACCESS_KEY_ID='test', AWS_SECRET_ACCESS_KEY='test', AWS_REGION='us-east-1',
           AWS_DEFAULT_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true',
           AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
           CI='true', LMAWS_LIVE='1')
checks = []
with tempfile.TemporaryDirectory(prefix='awssim-templates-') as tmp:
    for mode in ('open', 'enforce'):
        folder = Path(tmp) / mode
        folder.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        endpoint = f'http://127.0.0.1:{port}'
        env = {**ENV, 'LMAWS_ENDPOINT': endpoint, 'LMAWS_LEDGER_DIR': str(folder / 'ledgers'),
               'FLOCI_DOCKER_DOCKER_HOST': 'unix://' + str(folder / 'missing.sock')}
        def run(args):
            result = subprocess.run(args, cwd=folder, env=env, capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result.stdout
        run([ENGINE, '--no-preflight', str(ROOT / 'examples/templates.lms'), endpoint])
        payload = lambda name: json.loads((folder / (name + '.json')).read_text())
        assert isinstance(payload('receive')['MaxNumberOfMessages'], int)
        expected = 'hello "quoted" 雪 \\ path ${opaque}'
        assert payload('send')['MessageBody'] == expected
        (folder / 'schema.json').write_text(run([CLIENT, '--lmcode-schema', '--json']))
        shutil.copyfile(ROOT / 'examples/templates-client.lms', folder / 'client.lms')
        run([os.environ['LMAWS_COMPILER'], '--no-preflight', 'client.lms'])
        with (folder / 'server.log').open('w+') as log:
            server = subprocess.Popen(COMMAND + ['serve', '--port', str(port), '--auth', mode,
                                      '--auth-config', str(folder / 'auth.json')],
                                      env=env, cwd=folder, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 120
                while True:
                    assert server.poll() is None, 'server exited'
                    try:
                        with urllib.request.urlopen(endpoint + '/_floci/health', timeout=1): break
                    except OSError: pass
                    assert time.monotonic() < deadline, 'readiness timeout'
                    time.sleep(.2)
                def request(service, method, path='/', body=b'', target=None):
                    headers = {}
                    if target:
                        headers = {'Content-Type': 'application/x-amz-json-1.0', 'X-Amz-Target': 'AmazonSQS.' + target}
                    req = AWSRequest(method=method, url=endpoint + path, data=body, headers=headers)
                    SigV4Auth(Credentials('test', 'test'), service, 'us-east-1').add_auth(req)
                    wire = urllib.request.Request(req.url, data=body, method=method, headers=dict(req.headers))
                    try:
                        with urllib.request.urlopen(wire, timeout=20) as res: return res.status, res.read()
                    except urllib.error.HTTPError as res: return res.code, res.read()
                def sqs(action, file):
                    status, body = request('sqs', 'POST', body=(folder / file).read_bytes(), target=action)
                    return status, json.loads(body) if body else {}
                status, result = sqs('CreateQueue', 'create.json')
                assert status == 200 and result['QueueUrl'] == payload('send')['QueueUrl'], (status, result)
                status, result = sqs('SendMessage', 'send.json')
                assert status == 200 and result['MessageId'], (status, result)
                status, result = sqs('ReceiveMessage', 'receive.json')
                assert status == 200, (status, result)
                message = result['Messages'][0]
                assert message['Body'] == expected
                assert message['MessageAttributes']['note']['StringValue'] == 'context: ' + expected
                status, result = sqs('DeleteQueue', 'delete.json')
                if mode == 'open': assert status == 200, (status, result)
                else:
                    assert status == 403 and result['__type'] == 'AccessDeniedException', (status, result)
                    assert result['message'] == 'Access denied by policy'
                checks.append(mode + ': four SQS template requests, typed count, attributes and policy')
                run([CLIENT, '--live', '--profile', 'lmaws', '--lmcode', 'client.dslb'])
                objects = json.loads((folder / 'objects.json').read_text())
                assert objects[0]['key'] == 'quoted.txt', objects
                status, body = request('s3', 'GET', '/lmaws-templates-object/quoted.txt')
                assert status == 404, (status, body)
                status, body = request('s3', 'PUT', '/outside-template-policy')
                if mode == 'enforce':
                    assert status == 403 and ET.fromstring(body).findtext('Code') == 'AccessDenied', (status, body)
                else: assert status == 200, (status, body)
                checks.append(mode + ': lmaws template S3 put/list, cleanup and XML policy error')
            except BaseException:
                log.flush()
                print((folder / 'server.log').read_text(errors='replace'), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try: server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait()
                    raise AssertionError('server shutdown timeout')
print(json.dumps({'ok': True, 'engine': ENGINE, 'checks': checks}, indent=2))
