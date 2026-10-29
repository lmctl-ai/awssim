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
The HTTP listener is loopback-only. Upstream Docker-backed services such as Lambda
and RDS retain their Docker requirements and are outside the initial acceptance suite.

## Build and test

Requires Python 3.12+, Maven 3.9+, and GraalVM 25 (`JAVA_HOME` and `PATH`).

```sh
python3 scripts/build.py
python3 scripts/test-cli.py dist/awssim
python3 scripts/test-integration.py dist/awssim
```

For JVM development use `python3 scripts/build.py --jvm`, then
`java -jar build/upstream/target/quarkus-app/quarkus-run.jar serve`.
Builds fetch source once; cached source is checksum-verified on every build.
With Maven dependencies cached, builds and tests can run without internet access.
Tests use explicit loopback endpoints and dummy credentials.

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
