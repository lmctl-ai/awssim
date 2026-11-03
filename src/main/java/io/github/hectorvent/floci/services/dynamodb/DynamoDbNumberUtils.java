// Modified from floci 2.3.0 ee15f502bfc017861b67b076b82e1ccd032c7129
package io.github.hectorvent.floci.services.dynamodb;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.hectorvent.floci.core.common.AwsException;

import java.math.BigDecimal;

/**
 * DynamoDB number validation and normalization.
 * Numbers must have <= 38 significant digits, be within range, and are stored
 * in normalized form (no leading zeros, no trailing decimal zeros, -0 -> 0).
 */
final class DynamoDbNumberUtils {

    private static final BigDecimal MAX_ABS = new BigDecimal("1E+126");
    private static final BigDecimal MIN_ABS_NONZERO = new BigDecimal("1E-130");

    private DynamoDbNumberUtils() {}

    /**
     * Validates and normalizes a DynamoDB number string.
     * Throws ValidationException if the number is out of range or has too many significant digits.
     * Returns the normalized string (no leading zeros, no trailing decimal zeros).
     */
    static String validateAndNormalize(String numStr) {
        if (numStr == null || numStr.isEmpty()) {
            throw new AwsException("ValidationException",
                    "The parameter cannot be converted to a numeric value", 400);
        }
        requireStorableNumber(numStr, "");
        BigDecimal bd = new BigDecimal(numStr);
        BigDecimal stripped = bd.stripTrailingZeros();
        return toNormalizedString(stripped);
    }

    // AWS checks every number in a request before the table lookup. Single-item write
    // APIs wrap the error in the validation envelope, Query and Scan do not.
    static void requireStorable(JsonNode attributes, boolean inValidationEnvelope) {
        if (attributes == null || !attributes.isObject()) {
            return;
        }
        for (JsonNode value : attributes) {
            requireStorableValue(value, inValidationEnvelope ? "1 validation error detected: " : "");
        }
    }

    private static void requireStorableValue(JsonNode value, String prefix) {
        if (!value.isObject()) {
            return;
        }
        if (value.has("N")) {
            requireStorableNumber(value.get("N").asText(), prefix);
        }
        for (JsonNode n : value.path("NS")) {
            requireStorableNumber(n.asText(), prefix);
        }
        for (JsonNode element : value.path("L")) {
            requireStorableValue(element, prefix);
        }
        for (JsonNode entry : value.path("M")) {
            requireStorableValue(entry, prefix);
        }
    }

    static void requireStorableNumber(String numStr, String prefix) {
        if (numStr == null || numStr.isEmpty()) {
            throw new AwsException("ValidationException",
                    prefix + "The parameter cannot be converted to a numeric value: ", 400);
        }
        int eIdx = Math.max(numStr.indexOf('e'), numStr.indexOf('E'));
        if (eIdx >= 0) {
            String mantStr = numStr.substring(0, eIdx);
            String expStr = numStr.substring(eIdx + 1);
            BigDecimal mantBd;
            long exp;
            try {
                mantBd = new BigDecimal(mantStr);
                exp = Long.parseLong(expStr);
            } catch (NumberFormatException e) {
                throw new AwsException("ValidationException",
                        prefix + "The parameter cannot be converted to a numeric value: " + numStr, 400);
            }
            if (mantBd.signum() == 0) {
                if (exp > 125) {
                    throw new AwsException("ValidationException",
                            prefix + "Number overflow. Attempting to store a number with magnitude larger than supported range", 400);
                }
                if (exp < -130) {
                    throw new AwsException("ValidationException",
                            prefix + "Number underflow. Attempting to store a number with magnitude smaller than supported range", 400);
                }
                return;
            }
            BigDecimal strippedMant = mantBd.stripTrailingZeros();
            if (strippedMant.precision() > 38) {
                throw new AwsException("ValidationException",
                        prefix + "Attempting to store more than 38 significant digits in a Number", 400);
            }
            long highestPower = (long) strippedMant.precision() - (long) strippedMant.scale() - 1L + exp;
            if (highestPower > 125) {
                throw new AwsException("ValidationException",
                        prefix + "Number overflow. Attempting to store a number with magnitude larger than supported range", 400);
            }
            if (highestPower < -130) {
                throw new AwsException("ValidationException",
                        prefix + "Number underflow. Attempting to store a number with magnitude smaller than supported range", 400);
            }
            return;
        }
        BigDecimal bd;
        try {
            bd = new BigDecimal(numStr);
        } catch (NumberFormatException e) {
            throw new AwsException("ValidationException",
                    prefix + "The parameter cannot be converted to a numeric value: " + numStr, 400);
        }
        BigDecimal stripped = bd.stripTrailingZeros();
        if (stripped.precision() > 38) {
            throw new AwsException("ValidationException",
                    prefix + "Attempting to store more than 38 significant digits in a Number", 400);
        }
        BigDecimal abs = stripped.abs();
        if (abs.compareTo(MAX_ABS) >= 0) {
            throw new AwsException("ValidationException",
                    prefix + "Number overflow. Attempting to store a number with magnitude larger than supported range", 400);
        }
        if (abs.signum() > 0 && abs.compareTo(MIN_ABS_NONZERO) < 0) {
            throw new AwsException("ValidationException",
                    prefix + "Number underflow. Attempting to store a number with magnitude smaller than supported range", 400);
        }
    }

    // AWS answers with the overflow wording for any arithmetic result it cannot store,
    // a magnitude too small or too many significant digits included.
    static void checkArithmeticResult(BigDecimal result) {
        BigDecimal stripped = result.stripTrailingZeros();
        BigDecimal abs = stripped.abs();
        if (stripped.precision() > 38 || abs.compareTo(MAX_ABS) >= 0
                || (abs.signum() > 0 && abs.compareTo(MIN_ABS_NONZERO) < 0)) {
            throw new AwsException("ValidationException",
                    "Number overflow. Attempting to store a number with magnitude larger than supported range", 400);
        }
    }

    private static String toNormalizedString(BigDecimal bd) {
        // -0 -> 0
        if (bd.compareTo(BigDecimal.ZERO) == 0) {
            return "0";
        }
        // Use plain string to expand scientific notation to decimal form when reasonable
        return bd.toPlainString();
    }

    /**
     * Recursively normalizes all N-type number attribute values in the given item.
     * Returns a new ObjectNode with normalized values. Input must be a DynamoDB item
     * where attribute values are DynamoDB typed values (e.g. {S: "x"}, {N: "42"}).
     */
    static ObjectNode normalizeNumbersInItem(JsonNode item) {
        if (item == null || !item.isObject()) return (ObjectNode) item;
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        item.fields().forEachRemaining(entry -> {
            result.set(entry.getKey(), normalizeAttrValue(entry.getValue()));
        });
        return result;
    }

    private static JsonNode normalizeAttrValue(JsonNode attr) {
        if (attr == null) return null;

        // A well-formed AttributeValue has exactly one type member. Rebuilding a
        // malformed one (e.g. {"S":"x","N":"1"}) around its "N" member would launder
        // it into a valid value before validation can reject it — pass it through.
        if (attr.isObject() && attr.size() != 1) {
            return attr;
        }

        if (attr.has("N")) {
            String raw = attr.get("N").asText();
            String normalized = validateAndNormalize(raw);
            ObjectNode result = JsonNodeFactory.instance.objectNode();
            result.put("N", normalized);
            return result;
        }

        if (attr.has("NS")) {
            // Normalize and validate each element
            ObjectNode result = JsonNodeFactory.instance.objectNode();
            ArrayNode ns = JsonNodeFactory.instance.arrayNode();
            for (JsonNode elem : attr.get("NS")) {
                ns.add(validateAndNormalize(elem.asText()));
            }
            result.set("NS", ns);
            return result;
        }

        if (attr.has("M")) {
            ObjectNode result = JsonNodeFactory.instance.objectNode();
            result.set("M", normalizeNumbersInItem(attr.get("M")));
            return result;
        }

        if (attr.has("L")) {
            ObjectNode result = JsonNodeFactory.instance.objectNode();
            ArrayNode list = JsonNodeFactory.instance.arrayNode();
            for (JsonNode elem : attr.get("L")) {
                list.add(normalizeAttrValue(elem));
            }
            result.set("L", list);
            return result;
        }

        return attr;
    }
}
