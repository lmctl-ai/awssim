package ai.lmctl.awssim;

import io.github.hectorvent.floci.core.common.Digests;
import io.github.hectorvent.floci.core.common.auth.CredentialScope;
import io.github.hectorvent.floci.core.common.auth.SigV4RequestValidator;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.TreeMap;

/** Verifies the SigV4 chunk chain before upstream decodes the original wire body. */
final class ChunkSignatures {
    static void verify(byte[] body, String secret, CredentialScope scope, String date,
                       String seed, boolean trailers, String declaredTrailers) throws Exception {
        byte[] key = SigV4RequestValidator.deriveSigningKey(secret, scope.date(), scope.region(), scope.service());
        int position = 0;
        String previous = seed;
        while (true) {
            int end = lineEnd(body, position);
            String header = new String(body, position, end - position, StandardCharsets.US_ASCII);
            if (!header.matches("[0-9a-fA-F]+;chunk-signature=[0-9a-f]{64}")) throw new IllegalArgumentException();
            int size = Integer.parseInt(header.substring(0, header.indexOf(';')), 16);
            String signature = header.substring(header.indexOf('=') + 1);
            position = end + 2;
            if (size < 0 || size > body.length - position) throw new IllegalArgumentException();
            String toSign = "AWS4-HMAC-SHA256-PAYLOAD\n" + date + "\n" + scope.credentialScope() + "\n"
                    + previous + "\n" + Digests.sha256Hex(new byte[0]) + "\n"
                    + Digests.sha256Hex(Arrays.copyOfRange(body, position, position + size));
            check(key, toSign, signature);
            previous = signature;
            position += size;
            if (size == 0 && trailers) break;
            if (lineEnd(body, position) != position) throw new IllegalArgumentException();
            position += 2;
            if (size == 0) {
                if (position != body.length) throw new IllegalArgumentException();
                return;
            }
        }
        TreeMap<String, String> values = new TreeMap<>();
        String signature = null;
        while (true) {
            int end = lineEnd(body, position);
            if (end == position) { position += 2; break; }
            String line = new String(body, position, end - position, StandardCharsets.US_ASCII);
            int colon = line.indexOf(':');
            if (colon < 1) throw new IllegalArgumentException();
            String name = line.substring(0, colon), value = line.substring(colon + 1).trim();
            if (name.equals("x-amz-trailer-signature")) {
                if (signature != null) throw new IllegalArgumentException();
                signature = value;
            } else if (values.put(name, value) != null) throw new IllegalArgumentException();
            position = end + 2;
        }
        if (position != body.length || signature == null || declaredTrailers == null
                || !String.join(",", values.keySet()).equals(declaredTrailers)) throw new IllegalArgumentException();
        StringBuilder canonical = new StringBuilder();
        values.forEach((name, value) -> canonical.append(name).append(':').append(value).append('\n'));
        check(key, "AWS4-HMAC-SHA256-TRAILER\n" + date + "\n" + scope.credentialScope() + "\n"
                + previous + "\n" + Digests.sha256Hex(canonical.toString()), signature);
    }
    private static int lineEnd(byte[] body, int start) {
        for (int i = start; i + 1 < body.length && i - start < 8192; i++)
            if (body[i] == '\r' && body[i + 1] == '\n') return i;
        throw new IllegalArgumentException();
    }
    private static void check(byte[] key, String text, String signature) throws Exception {
        String expected = SigV4RequestValidator.hexEncode(SigV4RequestValidator.hmacSha256(key, text));
        if (!MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII), signature.getBytes(StandardCharsets.US_ASCII)))
            throw new IllegalArgumentException();
    }
}
