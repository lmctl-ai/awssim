# DynamoDB Parity Suite v3.9.0 Overlay (`parity-ddb`)

## Overview

This document records the DynamoDB conformance improvements implemented in the `awssim` overlay (`src/main/java/io/hector/floci/services/dynamodb/`) on top of upstream Floci `2.3.0` (`ee15f502bfc017861b67b076b82e1ccd032c7129`), measured against the [Parity Suite for DynamoDB](https://paritysuite.org) (`dynamodb-conformance` v3.9.0, commit `7a5d72fdb64b6b38f3ee95e6631d6a41d05fe520`).

## Baseline vs. Final Conformance Summary

| Tier | Total | Baseline Pass | Baseline Fail | Baseline Div | Final Pass | Final Fail | Skipped | Final Div |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Tier 1 (Core)** | 497 | 496 | 1 | 0.20% | **497** | **0** | 0 | **0.00%** |
| **Tier 2 (Complete)** | 452 | 417 | 28 | 6.19% | **445** | **0** | 7 | **0.00%** |
| **Tier 3 (Strict)** | 729 | 516 | 213 | 29.22% | **661** | **68** | 0 | **9.33%** |
| **Overall** | **1,678** | **1,429** | **242** | **14.42% (Grade B)** | **1,603** | **68** | **7** | **4.05% (Grade A)** |

- **174 of 242 baseline failures resolved (71.9% reduction in failures).**
- **100% pass rate on Tier 1 (Core) and Tier 2 (Complete)** (7 Tier 2 tests are intentionally skipped by the upstream suite).
- **43 of 57 Tier 3 test files pass 100%.**

## Overlay Classes and Fixes

All overlaid files are locked in `overlay.lock.json` against the SHA-256 digests of the pristine upstream Floci `2.3.0` tarball (`upstream.lock.json`) and carry the `// Modified from floci 2.3.0 ee15f502bfc017861b67b076b82e1ccd032c7129` header:

1. **`DynamoDbService.java`**
   - **Multi-attribute GSI key schemas (up to 4 HASH + 4 RANGE attributes)**: Supports `KeySchema` with up to 4 `HASH` and 4 `RANGE` elements on GSIs during `CreateTable` and `UpdateTable`, validates duplicate attribute names in a single `KeySchema`, and rejects `DeleteGlobalSecondaryIndexAction` when `GlobalSecondaryIndexes` is empty on the table.
   - **Query & Scan GSI validation**: Enforces multi-attribute GSI `KeyConditions` rejection (`Local Secondary indices and Global Secondary indices with multi-attribute keys are not supported with KeyConditions`), checks `ExclusiveStartKey` against both base table keys and full GSI key attributes, and orders `CreateTable` validation checks (table name -> empty `KeySchema` -> duplicate `AttributeDefinitions` -> `StreamSpecification` -> `BillingMode` -> key types -> table/GSI key resolution -> GSI projection rules).
   - **Empty expression maps**: Rejects empty `ExpressionAttributeNames` / `ExpressionAttributeValues` maps in `PutItem`, `DeleteItem`, and `UpdateItem` before evaluating conditions.

2. **`DynamoDbPartiQLParser.java`, `DynamoDbPartiQLHandler.java`, `DynamoDbPartiQLKeyPlan.java`, `PartiQLExecuteContext.java`**
   - **PartiQL SELECT / UPDATE / DELETE / INSERT parity**:
     - `SELECT VALUE expr` evaluation and projection (`VALUE` keyword support, scalar/nested extraction, missing path omission).
     - `SELECT` arithmetic (`+`, `-`, `*`, `/`, `%`), string concatenation (`||`), unary `+`/`-`, `CASE WHEN ... THEN ... ELSE ... END`, and `CAST(expr AS VARCHAR|NUMBER|BOOL)`.
     - Single-quoted string literals inside `WHERE` clauses and `SELECT` projections (while rejecting single-quoted top-level identifiers after `FROM` with `ValidationException`).
     - Rejects `OFFSET` (`Unexpected keyword OFFSET`), `JOIN` / comma joins (`JOIN is not supported`), `GROUP BY`, `HAVING`, `UNION` / `INTERSECT` / `EXCEPT`, `LIMIT` negative/float/boolean literals, trailing tokens, and empty statements with exact `<line>:<col>:<code description>` diagnostics.
     - Built-in function registry in `WHERE` and `SELECT`: `attribute_exists` (1 arg), `attribute_not_exists` (1 arg), `attribute_type` (2 args, validating type designator `S|N|B|SS|NS|BS|M|L|NULL|BOOL`), `begins_with` (2 args, rejecting non-string second arg), `contains` (2 args), `size` (1 arg), `missing_if` (2 args); rejects SQL functions (`UPPER`, `LOWER`, `COALESCE`, `SUBSTRING`, `COUNT`, `CAST` in `WHERE`, etc.) with `Unsupported function: <name>`.
     - Multi-attribute GSI `WHERE` validation: requires all GSI partition keys to be bound with equality/`IN` when `ORDER BY` or a sort-key condition is present (`Cannot execute query on part of the partition key`), requires leading GSI sort keys to be bound by `=` before constraining later sort keys (`Where clause does not contain a mandatory equality predicate on all key attributes`), and rejects `ORDER BY` mixing `ASC` and `DESC` across sort-key attributes (`Order by clause contains unsupported ordering on key attributes`).
     - Exact `Requested resource not found` message across PartiQL DML/query handlers when targeting a non-existent table.
     - Populates `ConsumedCapacity` on `ExecuteStatement`, `BatchExecuteStatement`, and `ExecuteTransaction` when `ReturnConsumedCapacity` is requested.

3. **`NativeDynamoDbJsonHandler.java`**
   - Validates PartiQL `Parameters` numbers at the wire boundary (rejecting `NaN`, `Infinity`, exponent overflow such as `1e2147483648`, >38 significant digits, and scale bounds) and sets structures (empty sets, duplicate elements, unpadded base64 in `BS`).
   - Validates `BatchExecuteStatement` statements list (`1..25` non-empty `Statement` entries), `ExecuteTransaction` (`1..100` entries, no `SELECT`, no duplicate target keys), and `ExecuteStatement` `Limit >= 1`.
   - Routes `TagResource`, `UntagResource`, and `ListTagsOfResource` validation cleanly for table ARNs.
   - Enforces vector search (`Knn`) query/index validation in `Query` and `Scan`.

4. **`DynamoDbItemSize.java` & `DynamoDbWriteCapacity.java`**
   - Corrected `SS` and `BS` attribute size calculation (sum of member byte lengths without per-element `+ 1` overhead).
   - Implemented the exact 400 KB (`409,600` bytes) `UpdateItem` statement charge formula (`3` base bytes + per-clause `SET`/`ADD`/`REMOVE`/`DELETE` path and operator charges) checked before `ConditionExpression`, while keeping post-update finished item size (`<= 409,600`) across `UpdateItem`, `AttributeUpdates`, PartiQL `UPDATE`, and `TransactWriteItems`.
   - Added item size calculated with target table key schema for `Key` in `GetItem`, `DeleteItem`, `UpdateItem`, `Query` (`ExclusiveStartKey`), `BatchGetItem`, `BatchWriteItem`, `TransactGetItems`, and `TransactWriteItems`.
   - Fixed `ConsumedCapacity` WCU calculation on `DeleteItem` and `UpdateItem` to use the max of pre-write and post-write item sizes.

5. **`DynamoDbNumberUtils.java` & `DynamoDbAttributeValueValidator.java`**
   - Enforced 32-bit signed integer exponent parsing prior to `BigDecimal` construction so `1e2147483648` (`Integer.MIN_VALUE` negation hazard in `BigDecimal`) is rejected with `Number overflow`.
   - Enforced maximum nesting depth of 32 levels across nested `L`/`M` structures (`ExpressionAttributeValues`, `Item`, `AttributeUpdates`, PartiQL literals and parameters).
   - Validated strict RFC 4648 base64 padding (`len % 4 == 0`) on `B` and `BS` values.

## Remaining Tier 3 Failure Groups (68 tests across 14 files)

All 68 remaining failures are in Tier 3 (`error-messages/` and `validation-ordering/`) and are recorded in `parity-ddb.lock.json` under `knownFailingTier3`:

1. **`tests/tier3/validation-ordering/*` (36 tests across 6 files: `deepRequests.test.ts`, `transactions.test.ts`, `batchOperations.test.ts`, `updateItem.test.ts`, `queryKeyCondition.test.ts`, `query.test.ts`)**:
   - Multi-error Coral bean-validation aggregation (`N validation errors detected: ...`) and exact pre-table vs. post-table check ordering when multiple fields are simultaneously invalid in a single request.
2. **`tests/tier3/error-messages/transactWriteItems.test.ts` (14 tests) & `transactGetItems.test.ts` (2 tests)**:
   - Indexed member path formatting (`transactItems.1.member.put.tableName`) and multi-constraint regex/length error aggregation on malformed table names inside transaction items.
3. **`tests/tier3/error-messages/queryKeyConditionOperands.test.ts` (5 tests)**:
   - Exact wording for `KeyConditionExpression` operand type mismatches (`Incorrect operand type for operator or function...`) vs. schema type mismatch wording.
4. **`tests/tier3/error-messages/deleteItem.test.ts` (4 tests), `putItem.test.ts` (2 tests), `updateItem.test.ts` (1 test), `batchGetItem.test.ts` (2 tests), `batchWriteItem.test.ts` (2 tests)**:
   - Exact Coral envelope prefix (`1 validation error detected: ...`) on single-item expression attribute map errors and map-key constraint messages on batch `RequestItems`.

## Running the Conformance Suite

```bash
source /home/ubuntu/tools/env.sh
python3 scripts/build.py --jvm
bash scripts/test-parity-ddb.sh
```

`scripts/test-parity-ddb.sh` pins commit `7a5d72fdb64b6b38f3ee95e6631d6a41d05fe520` from `parity-ddb.lock.json`, runs all 1,678 tests across Tiers 1–3 against `awssim` in open mode, and fails if any Tier 1 or Tier 2 test fails, if any of the 43 100%-passing Tier 3 files regress, or if any test not listed in `knownFailingTier3` fails.
