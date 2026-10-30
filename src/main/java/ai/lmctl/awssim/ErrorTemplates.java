package ai.lmctl.awssim;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Escape each value before insertion; inserted text is never rescanned. */
final class ErrorTemplates {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String XML = """
            <Error><Code>%s</Code><Message>%s</Message></Error>
            """;
    private static final String JSON_ERROR = """
            {"__type":%s,"message":%s}
            """;

    static String render(boolean xml, String code, String message) {
        return xml ? XML.formatted(escapeXml(code), escapeXml(message))
                : JSON_ERROR.formatted(quote(code), quote(message));
    }

    static String startup(String endpoint) {
        return """
                {"ok":true,"endpoint":%s}
                """.formatted(quote(endpoint)).stripTrailing();
    }

    static String failure(String kind, String message) {
        return """
                {"ok":false,"error":{"kind":%s,"message":%s}}
                """.formatted(quote(kind), quote(message)).stripTrailing();
    }

    private static String quote(String value) {
        try { return JSON.writeValueAsString(value); }
        catch (JsonProcessingException e) { throw new IllegalArgumentException("Invalid error value", e); }
    }

    private static String escapeXml(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&apos;");
    }
}
