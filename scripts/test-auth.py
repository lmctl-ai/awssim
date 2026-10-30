#!/usr/bin/env python3
"""Real SigV4 requests, IAM deny precedence, and lmaws failure propagation."""
import base64
import zlib
import datetime
import hashlib
import hmac
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

ROOT = Path(__file__).resolve().parents[1]
COMMAND = sys.argv[1:] or [str(ROOT / 'dist/awssim')]
COMMAND[0] = str(Path(COMMAND[0]).resolve())
ENV = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'FLOCI_', 'QUARKUS_', 'SIM_')) and 'proxy' not in k.lower()}
ENV.update(AWS_ACCESS_KEY_ID='test', AWS_SECRET_ACCESS_KEY='test', AWS_DEFAULT_REGION='us-east-1',
           AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', AWS_CONFIG_FILE=os.devnull,
           AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_PAGER='')
checks = []
with tempfile.TemporaryDirectory(prefix='awssim-auth-') as folder:
    folder = Path(folder)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    ENV['FLOCI_DOCKER_DOCKER_HOST'] = 'unix:///awssim-auth-missing.sock' if os.name == 'nt' else 'unix://' + str(folder / 'missing.sock')
    policy = {'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:Create*', 'dynamodb:Create*', 'sqs:Create*'],
         'Resource': ['arn:aws:s3:::awssim-*', 'arn:aws:dynamodb:*:000000000000:table/awssim-*', 'arn:aws:sqs:*:000000000000:awssim-*']},
        {'Effect': 'Deny', 'Action': '*:Create*', 'Resource': '*denied*'}]}
    config = {'credentials': [
        {'accessKeyId': 'test', 'secretAccessKey': 'test', 'policy': policy},
        {'accessKeyId': 'custom', 'secretAccessKey': 'unique-dummy-secret', 'policy': policy},
        {'accessKeyId': 'unrestricted', 'secretAccessKey': 'unrestricted-secret'},
        {'accessKeyId': 'empty', 'secretAccessKey': 'empty-secret', 'policy': {'Statement': []}}]}
    (folder / 'auth.json').write_text(json.dumps(config))
    bad = subprocess.run(COMMAND + ['serve', '--auth', 'enforce', '--auth-config', str(folder / 'missing.json')],
                         env=ENV, capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2 and 'invalid or unreadable auth config' in bad.stdout
    with (folder / 'server.log').open('w+') as log:
        process = subprocess.Popen(COMMAND + ['serve', '--port', str(port), '--auth', 'enforce', '--auth-config', str(folder / 'auth.json')], env=ENV, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 120
            while True:
                assert process.poll() is None, 'server exited'
                try:
                    with urllib.request.urlopen(endpoint + '/_floci/health', timeout=1): break
                except OSError: pass
                assert time.monotonic() < deadline, 'readiness timeout'
                time.sleep(.2)
            def request(service, name, key='test', secret='test', signed=True, tamper=False, stale=False, scope_service=None, streaming=None, malformed=False):
                method, path, body = ('PUT', '/' + name, b'') if service == 's3' else ('POST', '/', json.dumps(
                    {'QueueName': name} if service == 'sqs' else {'TableName': name, 'AttributeDefinitions': [{'AttributeName': 'pk', 'AttributeType': 'S'}],
                     'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}], 'BillingMode': 'PAY_PER_REQUEST'}, separators=(',', ':')).encode())
                date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1 if stale else 0)).strftime('%Y%m%dT%H%M%SZ')
                headers = {'host': f'127.0.0.1:{port}', 'x-amz-date': date, 'x-amz-content-sha256': hashlib.sha256(body).hexdigest()}
                if service != 's3':
                    headers['content-type'] = 'application/x-amz-json-1.0'
                    headers['x-amz-target'] = 'AmazonSQS.CreateQueue' if service == 'sqs' else 'DynamoDB_20120810.CreateTable'
                if streaming:
                    headers['x-amz-content-sha256'] = 'STREAMING-AWS4-HMAC-SHA256-PAYLOAD' + ('-TRAILER' if streaming == 'trailer' else '')
                    headers['content-encoding'] = 'aws-chunked'
                    headers['x-amz-decoded-content-length'] = '13'
                    if streaming == 'trailer': headers['x-amz-trailer'] = 'x-amz-checksum-crc32'
                names = ';'.join(sorted(headers))
                scope = f'{date[:8]}/us-east-1/{scope_service or service}/aws4_request'
                canonical = f'{method}\n{path}\n\n' + ''.join(f'{k}:{headers[k]}\n' for k in sorted(headers)) + f'\n{names}\n{headers["x-amz-content-sha256"]}'
                signing = ('AWS4' + secret).encode()
                for part in scope.split('/'): signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
                signature = hmac.new(signing, f'AWS4-HMAC-SHA256\n{date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}'.encode(), hashlib.sha256).hexdigest()
                if signed: headers['authorization'] = f'AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={names}, Signature={signature}'
                if malformed: headers['authorization'] = 'not-a-signature'
                if streaming:
                    previous = signature
                    chunks = []
                    data = b'chunk message'
                    for chunk in (data, b''):
                        string = f'AWS4-HMAC-SHA256-PAYLOAD\n{date}\n{scope}\n{previous}\n{hashlib.sha256(b"").hexdigest()}\n{hashlib.sha256(chunk).hexdigest()}'
                        previous = hmac.new(signing, string.encode(), hashlib.sha256).hexdigest()
                        chunks.append(f'{len(chunk):x};chunk-signature={previous}\r\n'.encode() + chunk + (b'\r\n' if chunk or streaming != 'trailer' else b''))
                    if streaming == 'trailer':
                        checksum = base64.b64encode(zlib.crc32(data).to_bytes(4, 'big')).decode()
                        trailer = f'x-amz-checksum-crc32:{checksum}\n'
                        string = f'AWS4-HMAC-SHA256-TRAILER\n{date}\n{scope}\n{previous}\n{hashlib.sha256(trailer.encode()).hexdigest()}'
                        trailer_signature = hmac.new(signing, string.encode(), hashlib.sha256).hexdigest()
                        chunks.append(trailer.replace('\n', '\r\n').encode() + f'x-amz-trailer-signature:{trailer_signature}\r\n\r\n'.encode())
                    body = b''.join(chunks)
                if tamper: body = body.replace(b'chunk message', b'chunk forgery') if streaming else body + b' '
                req = urllib.request.Request(endpoint + path, data=body, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=20) as res: return res.status, res.read().decode()
                except urllib.error.HTTPError as res: return res.code, res.read().decode()
            for service in ('s3', 'dynamodb', 'sqs'):
                for label, kwargs in [('missing', {'signed': False}), ('unknown', {'key': 'unknown'}), ('malformed', {'malformed': True}),
                                      ('wrong-secret', {'secret': 'wrong-dummy-secret'}), ('tampered', {'tamper': True}),
                                      ('expired', {'stale': True}), ('scope', {'scope_service': 'iam'}),
                                      ('empty-policy', {'key': 'empty', 'secret': 'empty-secret'})]:
                    status, body = request(service, 'awssim-' + label, **kwargs)
                    assert status == 403, (service, label, status, body)
                    assert ('<Code>' in body if service == 's3' else '__type' in json.loads(body)), body
                    assert 'wrong-dummy-secret' not in body and 'empty-secret' not in body
                    checks.append(service + ':' + label)
                for name, expected in [('awssim-allowed', 200), ('awssim-denied', 403), ('outside-prefix', 403)]:
                    status, body = request(service, name)
                    assert status == expected, (service, name, status, body)
                    if expected == 403: assert 'AccessDenied' in body
                    checks.append(service + ':' + name)
                status, body = request(service, 'awssim-custom', key='custom', secret='unique-dummy-secret')
                assert status == 200, (service, status, body)
                checks.append(service + ':configured-key')
            for mode in ('chunks', 'trailer'):
                for tamper in (False, True):
                    status, body = request('s3', 'awssim-allowed/' + mode, key='unrestricted', secret='unrestricted-secret', streaming=mode, tamper=tamper)
                    assert status == (403 if tamper else 200), (mode, tamper, status, body)
                    checks.append('S3 signed ' + mode + (' tampering rejected' if tamper else ' accepted'))
            if os.environ.get('LMAWS_BINARY'):
                client = str(Path(os.environ['LMAWS_BINARY']).resolve())
                (folder / 'schema.json').write_text(subprocess.check_output([client, '--dsl-schema', '--json'], text=True, timeout=30))
                compiler = os.environ.get('LMAWS_COMPILER') or shutil.which('lmscript')
                for kind in ('table', 'queue', 'bucket'):
                    (folder / 'deny.lms').write_text('p = _dsl_ schema="schema.json";\nlet cloud = aws.region("us-east-1");\nlet run = cloud.run("awssimauth", "owned.jsonl");\nlet item = run.' + kind + '("denied");\nend _dsl_\nsavedsl(p, "deny.dslb");\n')
                    compiled = subprocess.run([compiler, '--no-preflight', 'deny.lms'], cwd=folder, capture_output=True, text=True, timeout=60)
                    assert compiled.returncode == 0, (compiled.stdout, compiled.stderr)
                    (folder / 'owned.jsonl').unlink(missing_ok=True)
                    sdk = subprocess.run([client, '--live', '--profile', 'lmaws', '--dsl', 'deny.dslb'], cwd=folder,
                        env={**ENV, 'LMAWS_ENDPOINT': endpoint, 'LMAWS_LIVE': '1', 'CI': 'true', 'LMAWS_LEDGER_DIR': str(folder / 'ledgers')},
                        capture_output=True, text=True, timeout=120)
                    assert sdk.returncode == 3 and 'HTTP 403' in sdk.stdout + sdk.stderr, (sdk.returncode, sdk.stdout, sdk.stderr)
                    assert json.loads(sdk.stdout)['ok'] is False
                    assert 'unique-dummy-secret' not in sdk.stdout + sdk.stderr
                    checks.append('lmaws ' + kind + ' policy deny surfaced without secret leakage; exit=3')
                process.terminate(); process.wait(timeout=20)
                config['credentials'][0]['secretAccessKey'] = 'rotated-dummy-secret'
                (folder / 'auth.json').write_text(json.dumps(config))
                process = subprocess.Popen(COMMAND + ['serve', '--port', str(port), '--auth', 'enforce', '--auth-config', str(folder / 'auth.json')], env=ENV, stdout=log, stderr=log)
                deadline = time.monotonic() + 120
                while True:
                    assert process.poll() is None
                    try:
                        with urllib.request.urlopen(endpoint + '/_floci/health', timeout=1): break
                    except OSError: pass
                    assert time.monotonic() < deadline
                    time.sleep(.2)
                (folder / 'owned.jsonl').unlink(missing_ok=True)
                sdk = subprocess.run([client, '--live', '--profile', 'lmaws', '--dsl', 'deny.dslb'], cwd=folder,
                    env={**ENV, 'LMAWS_ENDPOINT': endpoint, 'LMAWS_LIVE': '1', 'CI': 'true', 'LMAWS_LEDGER_DIR': str(folder / 'ledgers2')},
                    capture_output=True, text=True, timeout=120)
                assert sdk.returncode == 3 and 'HTTP 403' in sdk.stdout, (sdk.returncode, sdk.stdout, sdk.stderr)
                assert json.loads(sdk.stdout)['ok'] is False
                assert 'rotated-dummy-secret' not in sdk.stdout + sdk.stderr
                status, body = request('s3', 'awssim-rotation')
                assert status == 403 and 'SignatureDoesNotMatch' in body
                checks.append('lmaws signature error surfaced without secret leakage; exit=3')
            log.flush()
            assert 'unique-dummy-secret' not in (folder / 'server.log').read_text(errors='replace')
        except BaseException:
            log.flush(); print((folder / 'server.log').read_text(errors='replace'), file=sys.stderr); raise
        finally:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
print(json.dumps({'ok': True, 'checks': checks}, indent=2))
