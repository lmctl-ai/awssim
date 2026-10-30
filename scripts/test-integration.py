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
COMMAND = sys.argv[1:] or [str(ROOT / 'dist' / 'awssim')]
if Path(COMMAND[0]).is_file():
    COMMAND[0] = str(Path(COMMAND[0]).resolve())
# Never consult a user's credentials, config, proxy, or cloud endpoint.
ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(('AWS_', 'FLOCI_', 'QUARKUS_')) and 'proxy' not in k.lower()}
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
    ENV['FLOCI_DOCKER_DOCKER_HOST'] = ('npipe:////./pipe/awssim-test-missing-' + str(os.getpid())
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
                                  cwd=directory, capture_output=True, text=True, timeout=45)
            assert busy.returncode == 3, (busy.returncode, busy.stdout, busy.stderr)
            assert json.loads(busy.stdout)['ok'] is False
            checks.append('occupied-port exit 3')

            aws('s3api', 'create-bucket', '--bucket', 'awssim-roundtrip')
            payload = b'awssim real S3 round trip\x00\xff\n'
            (directory / 'payload').write_bytes(payload)
            aws('s3api', 'put-object', '--bucket', 'awssim-roundtrip', '--key', 'test.bin', '--body', 'payload')
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
            message = aws('sqs', 'receive-message', '--queue-url', queue, '--wait-time-seconds', '1')['Messages'][0]
            assert message['Body'] == 'awssim queue round trip'
            aws('sqs', 'delete-message', '--queue-url', queue, '--receipt-handle', message['ReceiptHandle'])
            assert not aws('sqs', 'receive-message', '--queue-url', queue).get('Messages')
            aws('sqs', 'delete-queue', '--queue-url', queue)
            assert not aws('sqs', 'list-queues').get('QueueUrls')
            checks.append('SQS create/send/receive equality/delete message/delete queue')

            if os.environ.get('LMAWS_BINARY'):
                client = str(Path(os.environ['LMAWS_BINARY']).resolve())
                schema = subprocess.run([client, '--dsl-schema', '--json'],
                                        capture_output=True, text=True, check=True, timeout=30).stdout
                (directory / 'schema.json').write_text(schema)
                (directory / 'client.lms').write_text(
                    'p = _dsl_ schema="schema.json";\n'
                    'let cloud = aws.region("us-east-1"); check(!cloud.is_demo());\n'
                    'let run = cloud.run("awssimtest", "owned.jsonl");\n'
                    'let bucket = run.bucket("sdk"); run.put(bucket, "tiny.txt", "SDK S3");\n'
                    'let table = run.table("sdk"); run.put(table, "one", "SDK DynamoDB");\n'
                    'let queue = run.queue("sdk"); run.put(queue, "message", "SDK SQS");\n'
                    'check(run.remaining() == 3);\n'
                    'check(cloud.buckets().len() == 1); check(cloud.tables().len() == 1);\n'
                    'check(cloud.queues().len() == 1);\n'
                    'run.cleanup(); check(run.remaining() == 0);\n'
                    'end _dsl_\nsavedsl(p, "client.dslb");\n')
                compiled = subprocess.run([os.environ.get('LMAWS_COMPILER') or shutil.which('lmscript'), '--no-preflight', 'client.lms'],
                                          cwd=directory, capture_output=True, text=True, timeout=60)
                assert compiled.returncode == 0, (compiled.stdout, compiled.stderr)
                sdk_env = {**ENV, 'LMAWS_ENDPOINT': endpoint, 'LMAWS_LIVE': '1',
                           'AWS_PROFILE': 'lmaws', 'AWS_DEFAULT_PROFILE': 'lmaws',
                           'AWS_REGION': 'us-east-1', 'CI': 'true',
                           'LMAWS_LEDGER_DIR': str(directory / 'ledgers')}
                sdk = subprocess.run([client, '--live', '--profile', 'lmaws', '--dsl', 'client.dslb'],
                                     cwd=directory, env=sdk_env, capture_output=True, text=True, timeout=120)
                assert sdk.returncode == 0, (sdk.stdout, sdk.stderr)
                assert not aws('s3api', 'list-buckets')['Buckets']
                assert not aws('dynamodb', 'list-tables')['TableNames']
                assert not aws('sqs', 'list-queues').get('QueueUrls')
                checks.append('lmaws native SDK: three creates, writes, inventories, owned cleanup')
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
