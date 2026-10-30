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
