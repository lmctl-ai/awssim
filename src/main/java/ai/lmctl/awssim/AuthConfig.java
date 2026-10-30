package ai.lmctl.awssim;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/** Immutable, process-local configuration. Secrets never enter system properties or logs. */
public final class AuthConfig {
    public record Credential(String secret, String policy) {}
    static volatile Map<String, Credential> credentials;
    static void configure(String mode, String file) {
        if (!Set.of("open", "enforce").contains(mode)) throw new IllegalArgumentException("auth must be open or enforce");
        if (mode.equals("open")) { credentials = null; return; }
        try {
            if (file == null) throw new IllegalArgumentException();
            JsonNode root = new ObjectMapper().readTree(Path.of(file).toFile());
            if (!root.isObject() || root.size() != 1 || !root.path("credentials").isArray()
                    || root.path("credentials").isEmpty()) throw new IllegalArgumentException();
            Map<String, Credential> result = new HashMap<>();
            for (JsonNode entry : root.path("credentials")) {
                entry.fieldNames().forEachRemaining(k -> { if (!Set.of("accessKeyId", "secretAccessKey", "policy").contains(k)) throw new IllegalArgumentException(); });
                String key = entry.path("accessKeyId").asText("");
                String secret = entry.path("secretAccessKey").asText("");
                if (!key.matches("[A-Za-z0-9_-]+") || secret.isEmpty()) throw new IllegalArgumentException();
                JsonNode policy = entry.get("policy");
                if (policy != null) validatePolicy(policy);
                if (result.put(key, new Credential(secret, policy == null ? null : policy.toString())) != null) throw new IllegalArgumentException();
            }
            credentials = Map.copyOf(result);
            System.setProperty("floci.default-account-id", "000000000000");
        } catch (Exception e) { throw new IllegalArgumentException("invalid or unreadable auth config"); }
    }
    private static void validatePolicy(JsonNode policy) {
        policy.fieldNames().forEachRemaining(k -> { if (!Set.of("Version", "Statement").contains(k)) throw new IllegalArgumentException(); });
        if (!policy.isObject() || !policy.path("Statement").isArray()) throw new IllegalArgumentException();
        for (JsonNode s : policy.path("Statement")) {
            s.fieldNames().forEachRemaining(k -> { if (!Set.of("Sid", "Effect", "Action", "Resource").contains(k)) throw new IllegalArgumentException(); });
            if (!Set.of("Allow", "Deny").contains(s.path("Effect").asText())) throw new IllegalArgumentException();
            for (String k : Set.of("Action", "Resource")) {
                JsonNode v = s.path(k);
                if (v.isTextual() && !v.asText().isBlank()) continue;
                if (!v.isArray() || v.isEmpty()) throw new IllegalArgumentException();
                for (JsonNode item : v) if (!item.isTextual() || item.asText().isBlank()) throw new IllegalArgumentException();
            }
        }
    }
}
