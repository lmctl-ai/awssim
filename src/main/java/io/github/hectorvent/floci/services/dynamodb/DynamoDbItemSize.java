// Modified from floci 2.3.0 ee15f502bfc017861b67b076b82e1ccd032c7129
package io.github.hectorvent.floci.services.dynamodb;

import com.fasterxml.jackson.databind.JsonNode;
import io.github.hectorvent.floci.core.common.AwsException;
import io.github.hectorvent.floci.services.dynamodb.model.TableDefinition;
import io.github.hectorvent.floci.services.dynamodb.model.VectorIndex;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Calculates DynamoDB item size and enforces the 400KB limit.
 */
final class DynamoDbItemSize {

    static final int MAX_ITEM_SIZE = 400 * 1024; // 400 KB = 409,600 bytes

    static final String UPDATE_EXCEEDED = "Item size to update has exceeded the maximum allowed size";

    // UpdateItem measures what the statement writes, name and value, not the item it stores,
    // and charges these on top. Every other surface measures the finished item.
    private static final int UPDATE_COST = 3;
    private static final int SET_OR_ADD_COST = 19;
    private static final int REMOVE_OR_DELETE_COST = 2;
    private static final int LIST_INDEX_COST = 1;
    private static final int MAX_NUMBER_PAIRS = 20;

    private DynamoDbItemSize() {}

    /**
     * Validates the item fits within 400KB. Throws ValidationException if not.
     */
    static void validateSize(JsonNode item) {
        int size = calculateItemSize(item);
        if (size > MAX_ITEM_SIZE) {
            throw new AwsException("ValidationException",
                    "Item size has exceeded the maximum allowed size", 400);
        }
    }

    static boolean updatedItemWithinLimit(JsonNode item) {
        return calculateItemSize(item) <= MAX_ITEM_SIZE;
    }

    static boolean updateItemWithinLimit(JsonNode item, Set<String> writtenAttributes, int actionCost) {
        int total = 0;
        int written = 0;
        Iterator<Map.Entry<String, JsonNode>> fields = item.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> entry = fields.next();
            int size = utf8Length(entry.getKey()) + attributeValueSize(entry.getValue());
            total += size;
            if (writtenAttributes.contains(entry.getKey())) {
                written += size;
            }
        }
        return total <= MAX_ITEM_SIZE && written + actionCost <= MAX_ITEM_SIZE;
    }

    static boolean updateStatementWithinLimit(String expression, JsonNode exprAttrNames, JsonNode exprAttrValues) {
        if (expression == null || expression.isBlank()) {
            return true;
        }
        return updateExpressionStatementCharge(expression, exprAttrNames, exprAttrValues) <= MAX_ITEM_SIZE;
    }

    static int updateExpressionStatementCharge(String expression, JsonNode exprAttrNames, JsonNode exprAttrValues) {
        int cost = UPDATE_COST;
        String remaining = expression.trim().replaceAll("\\s+", " ");
        while (!remaining.isEmpty()) {
            int spaceIdx = remaining.indexOf(' ');
            if (spaceIdx < 0) break;
            String keyword = remaining.substring(0, spaceIdx).toUpperCase(Locale.ROOT);
            String body = remaining.substring(keyword.length()).trim();
            int nextClause = DynamoDbService.findNextClauseKeyword(body);
            String actions = nextClause < 0 ? body : body.substring(0, nextClause);
            remaining = nextClause < 0 ? "" : body.substring(nextClause);
            while (!actions.isBlank()) {
                int comma = DynamoDbService.findNextComma(actions);
                String action = (comma < 0 ? actions : actions.substring(0, comma)).trim();
                cost += statementActionCharge(keyword, action, exprAttrNames, exprAttrValues);
                actions = comma < 0 ? "" : actions.substring(comma + 1);
            }
        }
        return cost;
    }

    private static int statementActionCharge(String clause, String action,
                                             JsonNode exprAttrNames, JsonNode exprAttrValues) {
        return switch (clause) {
            case "SET" -> {
                int eq = action.indexOf('=');
                if (eq < 0) yield SET_OR_ADD_COST;
                String target = action.substring(0, eq).trim();
                String rhs = action.substring(eq + 1).trim();
                yield SET_OR_ADD_COST + targetPathCost(target, exprAttrNames)
                        + setRhsCharge(rhs, exprAttrNames, exprAttrValues);
            }
            case "ADD" -> {
                String[] parts = action.trim().split("\\s+", 2);
                String target = parts[0];
                JsonNode val = parts.length > 1 && exprAttrValues != null
                        ? exprAttrValues.get(parts[1].trim()) : null;
                yield SET_OR_ADD_COST + targetPathCost(target, exprAttrNames) + attributeValueSize(val);
            }
            case "DELETE" -> {
                String[] parts = action.trim().split("\\s+", 2);
                yield 1 + targetPathCost(parts[0], exprAttrNames);
            }
            case "REMOVE" -> 1 + targetPathCost(action.trim(), exprAttrNames);
            default -> 0;
        };
    }

    private static int setRhsCharge(String rhs, JsonNode exprAttrNames, JsonNode exprAttrValues) {
        int depth = 0;
        for (int i = 0; i < rhs.length(); i++) {
            char c = rhs.charAt(i);
            if (c == '(' || c == '[') depth++;
            else if (c == ')' || c == ']') depth--;
            else if (depth == 0 && (c == '+' || c == '-')) {
                String left = rhs.substring(0, i).trim();
                String right = rhs.substring(i + 1).trim();
                int opCost = (c == '+') ? 34 : 37;
                return opCost + operandCharge(left, exprAttrNames, exprAttrValues)
                        + operandCharge(right, exprAttrNames, exprAttrValues);
            }
        }
        int lparen = rhs.indexOf('(');
        if (lparen > 0 && rhs.endsWith(")")) {
            String fn = rhs.substring(0, lparen).trim().toLowerCase(Locale.ROOT);
            String inner = rhs.substring(lparen + 1, rhs.length() - 1);
            int comma = DynamoDbService.findNextComma(inner);
            if (comma >= 0) {
                String arg1 = inner.substring(0, comma).trim();
                String arg2 = inner.substring(comma + 1).trim();
                int fnCost = "if_not_exists".equals(fn) ? 39 : "list_append".equals(fn) ? 37 : 0;
                return fnCost + operandCharge(arg1, exprAttrNames, exprAttrValues)
                        + operandCharge(arg2, exprAttrNames, exprAttrValues);
            }
        }
        return operandCharge(rhs, exprAttrNames, exprAttrValues);
    }

    private static int operandCharge(String operand, JsonNode exprAttrNames, JsonNode exprAttrValues) {
        String op = operand.trim();
        if (op.startsWith(":")) {
            JsonNode val = exprAttrValues != null ? exprAttrValues.get(op) : null;
            return attributeValueSize(val);
        }
        java.util.List<Integer> segBytes = pathSegmentBytes(op, exprAttrNames);
        int total = 1;
        for (int b : segBytes) {
            total += 14 + b;
        }
        return total;
    }

    private static int targetPathCost(String path, JsonNode exprAttrNames) {
        java.util.List<Integer> segBytes = pathSegmentBytes(path.trim(), exprAttrNames);
        if (segBytes.isEmpty()) return 0;
        int total = segBytes.getFirst();
        for (int i = 1; i < segBytes.size(); i++) {
            total += 4 + segBytes.get(i);
        }
        return total;
    }

    private static java.util.List<Integer> pathSegmentBytes(String path, JsonNode exprAttrNames) {
        java.util.List<Integer> segments = new java.util.ArrayList<>();
        String[] dotParts = path.split("\\.");
        for (String part : dotParts) {
            String p = part.trim();
            if (p.isEmpty()) continue;
            int bracket = p.indexOf('[');
            String namePart = bracket < 0 ? p : p.substring(0, bracket).trim();
            if (!namePart.isEmpty()) {
                String resolved = resolveAttrName(namePart, exprAttrNames);
                segments.add(utf8Length(resolved));
            }
            int idx = bracket;
            while (idx >= 0) {
                int close = p.indexOf(']', idx);
                if (close < 0) break;
                String digits = p.substring(idx + 1, close).trim();
                segments.add(digits.length());
                idx = p.indexOf('[', close);
            }
        }
        return segments;
    }

    private static String resolveAttrName(String token, JsonNode exprAttrNames) {
        if (token.startsWith("#") && exprAttrNames != null && exprAttrNames.has(token)) {
            return exprAttrNames.get(token).asText();
        }
        return token;
    }

    static int calculateItemSize(JsonNode item) {
        if (item == null || !item.isObject()) return 0;
        int total = 0;
        Iterator<Map.Entry<String, JsonNode>> fields = item.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> entry = fields.next();
            total += utf8Length(entry.getKey());
            total += attributeValueSize(entry.getValue());
        }
        return total;
    }

    static int calculateBilledItemSize(TableDefinition table, JsonNode item) {
        if (item == null || !item.isObject()) return 0;
        if (table == null || table.getVectorIndexes().isEmpty()) {
            return calculateItemSize(item);
        }
        int total = 0;
        Iterator<Map.Entry<String, JsonNode>> fields = item.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> entry = fields.next();
            String attrName = entry.getKey();
            total += utf8Length(attrName);
            Long vectorDims = null;
            for (VectorIndex vi : table.getVectorIndexes()) {
                if (attrName.equals(vi.getVectorAttributeName())) {
                    vectorDims = vi.getDimensions();
                    break;
                }
            }
            if (vectorDims != null) {
                total += (int) (4L * vectorDims);
            } else {
                total += attributeValueSize(entry.getValue());
            }
        }
        return total;
    }

    static int attributeValueSize(JsonNode attr) {
        if (attr == null) return 0;
        if (attr.has("S")) return utf8Length(attr.get("S").asText());
        if (attr.has("N")) {
            return numberSize(attr.get("N").asText());
        }
        if (attr.has("B")) return binarySize(attr.get("B").asText());
        if (attr.has("BOOL")) return 1;
        if (attr.has("NULL")) return 1;
        if (attr.has("SS")) {
            int size = 0;
            for (JsonNode e : attr.get("SS")) size += utf8Length(e.asText());
            return size;
        }
        if (attr.has("NS")) {
            int size = 0;
            for (JsonNode e : attr.get("NS")) {
                size += numberSize(e.asText());
            }
            return size;
        }
        if (attr.has("BS")) {
            int size = 0;
            for (JsonNode e : attr.get("BS")) size += binarySize(e.asText());
            return size;
        }
        if (attr.has("L")) {
            int size = 3;
            for (JsonNode e : attr.get("L")) size += 1 + attributeValueSize(e);
            return size;
        }
        if (attr.has("M")) {
            int size = 3;
            Iterator<Map.Entry<String, JsonNode>> fields = attr.get("M").fields();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> entry = fields.next();
                size += 1 + utf8Length(entry.getKey()) + attributeValueSize(entry.getValue());
            }
            return size;
        }
        return 0;
    }

    // DynamoDB stores a number as base-100 digits paired from the decimal point, plus one byte,
    // plus one more for a negative value below the 20-pair maximum.
    static int numberSize(String number) {
        BigDecimal value;
        try {
            value = new BigDecimal(number).stripTrailingZeros();
        } catch (NumberFormatException | ArithmeticException ignored) {
            // A number BigDecimal cannot hold is never stored, so its size never matters.
            return number.length();
        }
        if (value.signum() == 0) {
            return 1;
        }
        long lowestPower = -(long) value.scale();
        long highestPower = value.precision() + lowestPower - 1;
        long pairs = Math.floorDiv(highestPower, 2) - Math.floorDiv(lowestPower, 2) + 1;
        return (int) (1 + pairs + (value.signum() < 0 && pairs < MAX_NUMBER_PAIRS ? 1 : 0));
    }

    static int utf8Length(String s) {
        if (s == null) return 0;
        return s.getBytes(StandardCharsets.UTF_8).length;
    }

    private static int binarySize(String base64) {
        if (base64 == null || base64.isEmpty()) return 0;
        try {
            return Base64.getDecoder().decode(base64).length;
        } catch (Exception e) {
            // Fallback: estimate from base64 length
            return (int) (base64.length() * 3L / 4);
        }
    }
}
