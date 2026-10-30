package ai.lmctl.awssim;

import io.quarkus.runtime.Quarkus;
import io.quarkus.runtime.QuarkusApplication;
import io.quarkus.runtime.annotations.QuarkusMain;

import java.nio.file.Path;
import java.nio.file.InvalidPathException;

/** The only overlay on upstream: argument handling before Quarkus starts. */
@QuarkusMain
public final class Main {
    private Main() {}

    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("--version")) {
            System.out.println("awssim @VERSION@ (floci @UPSTREAM@)");
            return;
        }
        if (args.length == 1 && (args[0].equals("--help") || args[0].equals("-h"))) {
            System.out.println("""
                awssim: local AWS, powered by Floci
                Usage: awssim serve|start [--port 4566] [--data-dir PATH] [--auth open|enforce] [--auth-config FILE]
                       awssim --version | --help
                Listens on 127.0.0.1. Foreground; Ctrl-C stops the server.
                Default storage is memory. --data-dir enables persistent storage.
                Health: http://127.0.0.1:4566/_floci/health
                AWS credentials: test/test; region: us-east-1
                """);
            return;
        }
        try {
            configure(args);
        } catch (IllegalArgumentException e) {
            failure("usage", e.getMessage(), 2);
            return;
        }
        Quarkus.run(Server.class, (code, error) -> {
            if (code != 0 || error != null) {
                if (error != null) {
                    error.printStackTrace(System.err);
                }
                failure("runtime", "emulator failed; see stderr", 3);
            }
        }, args);
    }

    public static final class Server implements QuarkusApplication {
        @Override
        public int run(String... args) {
            System.out.println(ErrorTemplates.startup("http://127.0.0.1:" + System.getProperty("floci.port")));
            Quarkus.waitForExit();
            return 0;
        }
    }

    static void configure(String[] args) {
        if (args.length == 0 || !(args[0].equals("serve") || args[0].equals("start"))) {
            throw new IllegalArgumentException("expected serve or start; use --help");
        }
        String authMode = System.getenv().getOrDefault("SIM_AUTH", "open");
        String authFile = System.getenv("SIM_AUTH_CONFIG");
        int port = 4566;
        String directory = null;
        boolean portSeen = false;
        for (int i = 1; i < args.length; i++) {
            String option = args[i];
            if (++i == args.length) {
                throw new IllegalArgumentException("missing option value");
            }
            switch (option) {
                case "--auth" -> authMode = args[i];
                case "--auth-config" -> authFile = args[i];
                case "--port" -> {
                    if (portSeen) {
                        throw new IllegalArgumentException("duplicate --port");
                    }
                    portSeen = true;
                    try {
                        port = Integer.parseInt(args[i]);
                    } catch (NumberFormatException e) {
                        throw new IllegalArgumentException("port must be an integer from 1 to 65535");
                    }
                    if (port < 1 || port > 65535) {
                        throw new IllegalArgumentException("port must be an integer from 1 to 65535");
                    }
                }
                case "--data-dir" -> {
                    if (directory != null || args[i].isBlank()) {
                        throw new IllegalArgumentException("--data-dir requires one nonempty path");
                    }
                    try {
                        directory = Path.of(args[i]).toAbsolutePath().normalize().toString();
                    } catch (InvalidPathException e) {
                        throw new IllegalArgumentException("invalid data directory path");
                    }
                }
                default -> throw new IllegalArgumentException("unknown option; use --help");
            }
        }
        AuthConfig.configure(authMode, authFile);
        System.setProperty("quarkus.http.host", "127.0.0.1");
        System.setProperty("quarkus.http.port", Integer.toString(port));
        System.setProperty("floci.port", Integer.toString(port));
        System.setProperty("floci.base-url", "http://127.0.0.1:" + port);
        System.setProperty("floci.hostname", "127.0.0.1");
        System.setProperty("floci.tls.enabled", "false");
        // Floci reconciles containers by namespace/port. Do not adopt a stock
        // Floci deployment's containers when this executable uses the same port.
        if (System.getenv("FLOCI_DOCKER_RESOURCE_NAMESPACE") == null
                && System.getProperty("floci.docker.resource-namespace") == null) {
            System.setProperty("floci.docker.resource-namespace", "awssim");
        }
        // Upstream EC2 starts a second, wildcard-bound IMDS listener at boot.
        // Keep the default process local; container users can explicitly opt in.
        if (System.getenv("FLOCI_SERVICES_EC2_ENABLED") == null
                && System.getProperty("floci.services.ec2.enabled") == null) {
            System.setProperty("floci.services.ec2.enabled", "false");
        }
        System.setProperty("quarkus.log.console.stderr", "true");
        System.setProperty("quarkus.banner.enabled", "false");
        System.setProperty("floci.storage.mode", directory == null ? "memory" : "persistent");
        System.setProperty("floci.storage.services.resourceexplorer2.mode", directory == null ? "memory" : "persistent");
        if (directory != null) {
            System.setProperty("floci.storage.persistent-path", directory);
        }
    }

    private static void failure(String kind, String message, int code) {
        // Messages are fixed strings, never arbitrary exception text or credentials.
        System.out.println(ErrorTemplates.failure(kind, message));
        System.exit(code);
    }
}
