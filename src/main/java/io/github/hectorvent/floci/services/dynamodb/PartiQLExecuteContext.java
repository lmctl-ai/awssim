// Modified from floci 2.3.0 ee15f502bfc017861b67b076b82e1ccd032c7129
package io.github.hectorvent.floci.services.dynamodb;

class PartiQLExecuteContext {

    private Integer limit;
    private String nextToken;
    private String tokenBinding;
    private boolean consistentRead;
    private String returnValuesOnConditionCheckFailure = "NONE";

    private PartiQLExecuteContext() {
    }

    static PartiQLExecuteContext builder() {
        return new PartiQLExecuteContext();
    }

    PartiQLExecuteContext limit(Integer limit) {
        this.limit = limit;
        return this;
    }

    PartiQLExecuteContext nextToken(String nextToken) {
        this.nextToken = nextToken;
        return this;
    }

    PartiQLExecuteContext tokenBinding(String tokenBinding) {
        this.tokenBinding = tokenBinding;
        return this;
    }

    PartiQLExecuteContext consistentRead(boolean consistentRead) {
        this.consistentRead = consistentRead;
        return this;
    }

    PartiQLExecuteContext returnValuesOnConditionCheckFailure(String returnValuesOnConditionCheckFailure) {
        this.returnValuesOnConditionCheckFailure = returnValuesOnConditionCheckFailure != null
                ? returnValuesOnConditionCheckFailure : "NONE";
        return this;
    }

    boolean consistentRead() {
        return consistentRead;
    }

    String returnValuesOnConditionCheckFailure() {
        return returnValuesOnConditionCheckFailure;
    }

    Integer limit() {
        return limit;
    }

    String nextToken() {
        return nextToken;
    }

    String tokenBinding() {
        return tokenBinding;
    }
}
