#!/usr/bin/env python3
"""Real AWS CLI S3/DynamoDB/SQS lifecycle tests against our own process."""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [str(Path(a).resolve()) if Path(a).is_file() else a for a in (sys.argv[1:] or [str(ROOT / 'dist' / 'awssim')])]
# Never consult a user's credentials, config, proxy, or cloud endpoint.
ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(('AWS_', 'FLOCI_', 'QUARKUS_', 'SIM_')) and 'proxy' not in k.lower()}
ENV.update(AWS_ACCESS_KEY_ID='test', AWS_SECRET_ACCESS_KEY='test',
           AWS_DEFAULT_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true',
           AWS_PAGER='', AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
endpoint = f'http://127.0.0.1:{port}'
checks = []
with tempfile.TemporaryDirectory(prefix='awssim-test-') as directory:
    directory = Path(directory)
    if os.environ.get('AWSSIM_TEST_AUTH') == '1':
        auth = {'credentials': [{'accessKeyId': 'test', 'secretAccessKey': 'test', 'policy': {'Statement': [{'Effect': 'Allow', 'Action': ['s3:*', 'dynamodb:*', 'sqs:*'], 'Resource': '*'}]}}]}
        (directory / 'auth.json').write_text(json.dumps(auth))
        ENV.update(SIM_AUTH='enforce', SIM_AUTH_CONFIG=str(directory / 'auth.json'))
    ENV['FLOCI_DOCKER_DOCKER_HOST'] = ('unix:///awssim-test-missing-' + str(os.getpid()) + '.sock'
                                         if os.name == 'nt' else 'unix://' + str(directory / 'missing-docker.sock'))
    def aws(*args):
        p = subprocess.run(['aws', '--endpoint-url', endpoint, '--output', 'json',
                            '--cli-connect-timeout', '5', '--cli-read-timeout', '15', *args],
                           env=ENV, cwd=directory, capture_output=True, text=True, timeout=45)
        assert p.returncode == 0, (args, p.stdout, p.stderr)
        return json.loads(p.stdout) if p.stdout.strip() else {}

    with (directory / 'server.log').open('w+') as log:
        def start_server(*options):
            process = subprocess.Popen(COMMAND + ['serve', '--port', str(port), *options],
                                       env=ENV, cwd=directory, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 90
                while True:
                    assert process.poll() is None, 'server exited before readiness'
                    try:
                        with urllib.request.urlopen(endpoint + '/_floci/health', timeout=1) as response:
                            if response.status == 200:
                                return process
                    except (OSError, TimeoutError):
                        pass
                    assert time.monotonic() < deadline, 'readiness timeout'
                    time.sleep(0.2)
            except BaseException:
                stop_server(process)
                raise

        def stop_server(process):
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AssertionError('server failed to stop within 20 seconds')

        server = None
        try:
            server = start_server()
            # A second process must report startup failure, never silently choose another port.
            busy = subprocess.run(COMMAND + ['start', '--port', str(port)], env=ENV,
                                  cwd=directory, capture_output=True, text=True, timeout=120)
            assert busy.returncode == 3, (busy.returncode, busy.stdout, busy.stderr)
            assert json.loads(busy.stdout)['ok'] is False
            checks.append('occupied-port exit 3')

            aws('s3api', 'create-bucket', '--bucket', 'awssim-roundtrip')
            # us-east-1 re-create of an owned bucket is idempotent (200), whereas non-us-east-1 returns BucketAlreadyOwnedByYou.
            aws('s3api', 'create-bucket', '--bucket', 'awssim-roundtrip')
            aws('s3api', 'create-bucket', '--bucket', 'awssim-eu-bucket', '--region', 'eu-west-1',
                '--create-bucket-configuration', 'LocationConstraint=eu-west-1')
            dup_eu = subprocess.run(['aws', '--endpoint-url', endpoint, 's3api', 'create-bucket',
                                     '--bucket', 'awssim-eu-bucket', '--region', 'eu-west-1',
                                     '--create-bucket-configuration', 'LocationConstraint=eu-west-1'],
                                    cwd=directory, env=ENV, capture_output=True, text=True, timeout=60)
            assert dup_eu.returncode != 0 and 'BucketAlreadyOwnedByYou' in dup_eu.stderr, (dup_eu.returncode, dup_eu.stderr)
            aws('s3api', 'delete-bucket', '--bucket', 'awssim-eu-bucket', '--region', 'eu-west-1')
            checks.append('S3 us-east-1 idempotent CreateBucket vs non-us-east-1 BucketAlreadyOwnedByYou')

            payload = b'awssim real S3 round trip\x00\xff\n'
            (directory / 'payload').write_bytes(payload)
            aws('s3api', 'put-object', '--bucket', 'awssim-roundtrip', '--key', 'test.bin', '--body', 'payload',
                '--metadata', 'build_id=42')
            head_meta = aws('s3api', 'head-object', '--bucket', 'awssim-roundtrip', '--key', 'test.bin')
            assert head_meta.get('Metadata', {}).get('build_id') == '42', head_meta
            aws('s3api', 'get-object', '--bucket', 'awssim-roundtrip', '--key', 'test.bin', 'received')
            assert (directory / 'received').read_bytes() == payload
            aws('s3api', 'delete-object', '--bucket', 'awssim-roundtrip', '--key', 'test.bin')
            aws('s3api', 'delete-bucket', '--bucket', 'awssim-roundtrip')
            assert not aws('s3api', 'list-buckets')['Buckets']
            checks.append('S3 create/put/get byte equality/delete')

            aws('dynamodb', 'create-table', '--table-name', 'awssim-roundtrip',
                '--attribute-definitions', 'AttributeName=pk,AttributeType=S',
                '--key-schema', 'AttributeName=pk,KeyType=HASH', '--billing-mode', 'PAY_PER_REQUEST')
            item = {'pk': {'S': 'one'}, 'value': {'S': 'awssim'}}
            # File transport also works with Windows command-line quoting.
            (directory / 'item.json').write_text(json.dumps(item))
            (directory / 'key.json').write_text(json.dumps({'pk': {'S': 'one'}}))
            aws('dynamodb', 'put-item', '--table-name', 'awssim-roundtrip', '--item', 'file://item.json')
            assert aws('dynamodb', 'get-item', '--table-name', 'awssim-roundtrip',
                       '--key', 'file://key.json', '--consistent-read')['Item'] == item
            aws('dynamodb', 'delete-table', '--table-name', 'awssim-roundtrip')
            assert not aws('dynamodb', 'list-tables')['TableNames']
            checks.append('DynamoDB create/put/get equality/delete')

            queue = aws('sqs', 'create-queue', '--queue-name', 'awssim-roundtrip')['QueueUrl']
            assert queue.startswith(endpoint + '/'), queue
            aws('sqs', 'send-message', '--queue-url', queue, '--message-body', 'awssim queue round trip')
            message = aws('sqs', 'receive-message', '--queue-url', queue, '--visibility-timeout', '30', '--wait-time-seconds', '1')['Messages'][0]
            assert message['Body'] == 'awssim queue round trip'
            aws('sqs', 'change-message-visibility', '--queue-url', queue, '--receipt-handle', message['ReceiptHandle'], '--visibility-timeout', '0')
            message = aws('sqs', 'receive-message', '--queue-url', queue, '--wait-time-seconds', '1')['Messages'][0]
            assert message['Body'] == 'awssim queue round trip'
            aws('sqs', 'delete-message', '--queue-url', queue, '--receipt-handle', message['ReceiptHandle'])
            assert not aws('sqs', 'receive-message', '--queue-url', queue).get('Messages')
            aws('sqs', 'delete-queue', '--queue-url', queue)
            gone_q = subprocess.run(['aws', '--endpoint-url', endpoint, 'sqs', 'get-queue-url', '--queue-name', 'awssim-roundtrip'],
                                    cwd=directory, env=ENV, capture_output=True, text=True, timeout=60)
            assert gone_q.returncode != 0 and 'NonExistentQueue' in gone_q.stderr, (gone_q.returncode, gone_q.stderr)
            assert not aws('sqs', 'list-queues').get('QueueUrls')
            checks.append('SQS create/send/receive/nack(0)/delete message/delete queue')

            if os.environ.get('LMAWS_BINARY'):
                client = str(Path(os.environ['LMAWS_BINARY']).resolve())
                probe = subprocess.run([client, '--lmcode-schema', '--json'], capture_output=True, text=True, timeout=30)
                if probe.returncode != 0:
                    print('SKIP lmaws SDK lmcode integration: pinned host lacks current --lmcode-schema discovery')
                else:
                    (directory / 'schema.json').write_text(probe.stdout)
                    client_source = (
                        'program = _lmcode_ schema="schema.json";\n'
                        'let cloud = aws.region("us-east-1"); check(!cloud.is_demo());\n'
                        'let run = cloud.run("awssimtest", "owned.jsonl");\n'
                        'let bucket = run.bucket("sdk"); run.put(bucket, "tiny.txt", "SDK S3");\n'
                        'let table = run.table("sdk"); run.put(table, "one", "SDK DynamoDB");\n'
                        'let queue = run.queue("sdk"); run.put(queue, "message", "SDK SQS");\n'
                        'check(run.remaining() == 3);\n'
                        'check(cloud.buckets().len() == 1); check(cloud.tables().len() == 1);\n'
                        'check(cloud.queues().len() == 1); run.cleanup(); check(run.remaining() == 0);\n'
                        'end _lmcode_\n'
                        'let result = run_lmcode(program, {command:[' + json.dumps(client) + ', "--live", "--profile", "lmaws", "--lmcode", "$DSLFILE"]});\n'
                        'assert(result.ok, "lmaws integration failed: " + json_stringify(result));\n'
                        'println(json_stringify(result));\n')
                    (directory / 'client.lms').write_text(client_source)
                    compiler = os.environ.get('LMSCRIPT_BIN') or shutil.which('lmscript')
                    assert compiler, 'LMSCRIPT_BIN is required for the current lmcode integration'
                    sdk_env = {**ENV, 'LMAWS_ENDPOINT': endpoint, 'LMAWS_LIVE': '1',
                               'AWS_PROFILE': 'lmaws', 'AWS_DEFAULT_PROFILE': 'lmaws',
                               'AWS_REGION': 'us-east-1', 'CI': 'true',
                               'LMAWS_LEDGER_DIR': str(directory / 'ledgers')}
                    sdk = subprocess.run([compiler, 'client.lms'], cwd=directory, env=sdk_env,
                                         capture_output=True, text=True, timeout=120)
                    assert sdk.returncode == 0, (sdk.stdout, sdk.stderr)
                    assert not aws('s3api', 'list-buckets')['Buckets']
                    assert not aws('dynamodb', 'list-tables')['TableNames']
                    assert not aws('sqs', 'list-queues').get('QueueUrls')
                    checks.append('lmaws native lmcode SDK: three creates, writes, inventories, owned cleanup')
            stop_server(server)
            server = start_server('--data-dir', str(directory / 'state'))
            aws('s3api', 'create-bucket', '--bucket', 'awssim-persist')
            aws('s3api', 'put-object', '--bucket', 'awssim-persist', '--key', 'saved', '--body', 'payload')
            stop_server(server)
            server = start_server('--data-dir', str(directory / 'state'))
            aws('s3api', 'get-object', '--bucket', 'awssim-persist', '--key', 'saved', 'persisted')
            assert (directory / 'persisted').read_bytes() == payload
            aws('s3api', 'delete-object', '--bucket', 'awssim-persist', '--key', 'saved')
            aws('s3api', 'delete-bucket', '--bucket', 'awssim-persist')
            checks.append('persistent S3 bytes survive graceful restart')
        except BaseException:
            log.flush()
            print((directory / 'server.log').read_text(errors='replace'), file=sys.stderr)
            raise
        finally:
            if server is not None:
                stop_server(server)
    print(json.dumps({'ok': True, 'client': 'aws-cli', 'checks': checks, 'leftovers': 0}, indent=2))
