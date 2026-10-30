# awssim

Local AWS emulator on `127.0.0.1:4566`, built on [Floci](https://github.com/floci-io/floci).
One native executable. S3, DynamoDB and SQS need neither Java nor Docker at runtime.

```sh
awssim --version
awssim serve
# In another terminal (AWS CLI installed):
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
aws --endpoint-url http://127.0.0.1:4566 s3 mb s3://hello-awssim
aws --endpoint-url http://127.0.0.1:4566 s3 ls
```

`start` is an alias for foreground `serve`; stop with Ctrl-C. Use `--port 4567`
for another port, or `--data-dir ./state` to persist state across restarts.
Without `--data-dir`, storage is in memory. Health is `GET /_floci/health`.
The HTTP listener is loopback-only. EC2 is disabled by default because upstream
starts an additional wildcard-bound IMDS listener on port 9169. Set
`FLOCI_SERVICES_EC2_ENABLED=true` to opt into that container-oriented behavior. Upstream Docker-backed services such as Lambda
and RDS retain their Docker requirements and are outside the initial acceptance suite.
Docker resources default to the `awssim` namespace so startup reconciliation cannot
adopt a stock Floci deployment's containers on the same port. Advanced users can
set `FLOCI_DOCKER_RESOURCE_NAMESPACE` explicitly. An unreachable TCP Docker endpoint
inherits upstream's retry delays. Missing local sockets fail fast on POSIX;
Windows can take about a minute to start without Docker because of upstream retries.
Windows named-pipe Docker transport is deferred: the pinned upstream native image
lacks required JNA symbols. The standard no-Docker configuration uses an absent
Unix socket and the core AWS services remain available.

## Optional authentication and permissions

Open mode is the default. To enforce configured dummy credentials and policies:

```sh
awssim serve --auth enforce --auth-config examples/auth.json
# Equivalent: SIM_AUTH=enforce SIM_AUTH_CONFIG=examples/auth.json awssim serve
```

The example accepts `test/test`, allows the three supported services, and denies
Delete actions on resources whose ARN contains `protected`. Use dummy secrets only.
CLI options override environment. The config is loaded once at startup; restart to
rotate it. Invalid/missing config exits 2 without printing its contents.

Each `credentials` entry has `accessKeyId`, `secretAccessKey`, and an optional IAM
`policy`. Omit policy for authentication only. A present policy defaults to deny;
an empty `Statement` denies everything. Statements accept `Effect`, `Action`,
`Resource`, and optional `Sid`; Action/Resource accept strings or arrays with `*`
and `?` wildcards. Explicit Deny wins. Unknown policy/config fields fail startup.
Policies are attached to each configured key, including `test`; there is no admin
bypass. This reuses Floci's IAM evaluator and service action/resource mappings.

Enforced mode supports header SigV4 for path-style S3 and DynamoDB/SQS JSON APIs,
including SDK v2 signed S3 chunks/trailers. Supported operations are listed below. It checks timestamps within 15 minutes,
payload hashes when signed, and the chunk signature chain. S3 unsigned-payload
modes retain AWS's unsigned-body semantics. Bodies are capped at 16 MiB encoded.
The account is fixed to `000000000000`. Requests receive HTTP 403 with S3 XML or
AWS JSON errors. Only `GET /_floci/health` is anonymous; unsupported routes fail closed.

| Service | Enforced operations |
| --- | --- |
| S3 | Create/Delete bucket, List buckets/objects, Head bucket/object, Get/Put/Delete object, Get bucket location, bucket tags |
| DynamoDB | Create/Describe/Delete/List table, Put/Get/Delete/Update item, Query/Scan, resource tags |
| SQS | Create/Delete/Get/List queue, Get attributes, Send/Receive/Delete message, queue tags |

S3 copy, POST/batch/multipart operations, versioned requests, encoded slashes and
empty/dot path segments are rejected in enforced mode. This avoids claiming checks
for secondary resources that this subset does not authorize.

Presigned URLs, session tokens, SQS Query protocol, other services, and advanced IAM
conditions/principals/resource policies are deferred. This is a local test subset,
not a production IAM implementation. See [auth lessons](durable-memory/auth-2026-10-30.md).

```sh
# Existing open-mode suite, then the same operations with enforcement:
python3 scripts/test-integration.py dist/awssim
AWSSIM_TEST_AUTH=1 python3 scripts/test-integration.py dist/awssim
python3 scripts/test-auth.py dist/awssim
```

CI pins and checksum-verifies lmaws v0.1.27 on Linux and macOS, running real SDK
writes/cleanup with enforcement plus denied-policy and wrong-secret cases. lmaws
returns exit 3 and `ok:false` for denials; policy errors can surface as generic
`AWS_SERVICE_ERROR (HTTP 403)`. Windows uses AWS CLI and signed HTTP because that
client release has no Windows asset. No client or compiler is needed at runtime.

## Build and test

Requires Python 3.12+, Maven 3.9+, and GraalVM 25 (`JAVA_HOME` and `PATH`).
Native compilation defaults to a 7 GiB heap; allow at least 10 GiB free RAM.
Set `NATIVE_XMX` to change the build heap budget. On memory-constrained macOS ARM64
runners, CI uses `NATIVE_XMX=5g NATIVE_USE_G1=1 NATIVE_DETACHED=1`: Maven
first prepares Quarkus native sources and exits, then `native-image` runs with
the generated arguments. This avoids keeping Maven resident during compilation.

```sh
python3 scripts/build.py
python3 scripts/test-cli.py dist/awssim
python3 scripts/test-integration.py dist/awssim
```

For JVM development use `python3 scripts/build.py --jvm`, then
`java -jar build/upstream-jvm/target/quarkus-app/quarkus-run.jar serve`.
Builds fetch source once; cached source is checksum-verified on every build.
With Maven dependencies cached, `python3 scripts/build.py --offline` and tests run without internet access.
Tests require AWS CLI (`aws`) and use explicit loopback endpoints and dummy credentials.
They force a nonexistent Docker socket and verify S3 bytes, DynamoDB items, SQS messages, occupied-port errors, graceful
shutdown, persistence across restart, and deletion of test resources.

To additionally exercise the released lmaws Java SDK host, set `LMAWS_BINARY` to a
checksum-verified lmaws v0.1.27 binary and `LMAWS_COMPILER` to lmscript 0.2.420.
That release reads bytecode v5; the current compiler emits v6. The test exports
its schema, compiles a small program, creates and writes all three resource types,
checks inventories, cleans up, and independently verifies absence with AWS CLI.
The compiler and client are test dependencies only.

## Design

`upstream.lock.json` pins Floci 2.3.0, its Git commit, and source archive SHA-256.
The release had no attached native assets when this project was created. We compile
that source plus one small CLI overlay into a native image, retaining upstream's
native metadata and service implementation. We do not fork-mirror Floci or download
an emulator at runtime. `scripts/build.py` stages source under ignored `build/`.

Exit codes: 0 for success, 2 for invalid arguments, 3 for server startup failure.
CLI errors use `{"ok":false,"error":{"kind":"usage","message":"..."}}`.
Version/help are plain text; startup emits an `ok:true` endpoint envelope and server diagnostics go to stderr. AWS responses keep
upstream's AWS wire formats. CLI port/storage settings take precedence over environment;
other Floci environment configuration remains available. S3 SDK clients should use
path-style addressing.

Compatibility follows the pinned upstream; passing three service lifecycles does not
imply full AWS parity. Native builds and release publishing are owned by this repository.
MIT; upstream attribution is retained in `FLOCI-LICENSE`.

## CI and releases

Main pushes build and test Linux x64/arm64, macOS x64/arm64 and Windows x64.
Tags matching `v*` publish binaries and SHA256SUMS after all platform jobs pass.
macOS Intel uses GraalVM 25.0.1; other platforms use 25.0.2. Linux CI builds on
Ubuntu 22.04 and requires glibc 2.35+ and libz; Windows needs the Visual C++ runtime. All five platforms have passed native CLI and AWS service acceptance tests.
