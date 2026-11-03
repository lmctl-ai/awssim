# Parity Suite DynamoDB Conformance — Baseline Report

- **Date**: 2026-11-02
- **Target**: `awssim 0.2.1` (`a50ae4d`, pinned upstream Floci `2.3.0` commit `ee15f502bfc017861b67b076b82e1ccd032c7129`)
- **Suite**: [Parity Suite DynamoDB Conformance](https://paritysuite.org) v3.9.0 (`7a5d72fdb64b6b38f3ee95e6631d6a41d05fe520`)
- **Mode**: JVM (`build/upstream-jvm/target/quarkus-app/quarkus-run.jar serve --port 4599`, `--auth open`)

## 1. Baseline Score Summary

| Tier | Total | Pass | Fail | Skip | Indeterminate | Divergence (`Fail / Total`) | Coverage (`(Pass + Fail) / Total`) | Pass Rate (`Pass / (Pass + Fail)`) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Tier 1 (Core)** | 497 | 496 | 1 | 0 | 0 | 0.20% | 100.00% | 99.80% |
| **Tier 2 (Complete)** | 452 | 417 | 28 | 7 | 0 | 6.19% | 98.45% | 93.71% |
| **Tier 3 (Strict)** | 729 | 516 | 213 | 0 | 0 | 29.22% | 100.00% | 70.78% |
| **Overall** | **1,678** | **1,429** | **242** | **7** | **0** | **14.42%** | **99.58%** | **85.52%** |

- **Overall Grade**: **B** (14.42% divergence, 99.58% coverage; effective divergence for grading `14.42% + (100% - 99.58%) / 3 = 14.56% < 15.0%`).
- **Comparison with published Floci (`5ee85db604c4`)**: Upstream Floci's later commit `5ee85db604c4` on the Parity Suite board scores 9.3% divergence (1,518 pass, 156 fail, 4 skip; 0.0% Tier 1, 0.0% Tier 2, 21.4% Tier 3). The 86-test delta between pinned Floci `2.3.0` (`ee15f502bfc0`) and `5ee85db604c4` is concentrated in Tier 1 (1 test), Tier 2 (28 tests across PartiQL and Vector Search capacity), and Tier 3 PartiQL/limit/error-message tests added or refined after Floci 2.3.0.

---

## 2. Failure & Skip Triage by Tier and File

### 2.1 Tier 1 Failures (1 total across 1 file)

| File | Pass | Fail | Failing Test | Root Cause Group |
| --- | ---: | ---: | --- | --- |
| `tests/tier1/updateItem/basic.test.ts` | 36 | 1 | `UpdateItem — ADD ADD of an empty string member onto an existing set adds it` | **G1 (UpdateItem ADD empty string in SS)**: `ADD` of an empty string `""` in a String Set (`SS`) onto an existing `SS` attribute is rejected or dropped instead of adding `""` to the set. |

### 2.2 Tier 2 Failures (28 total across 6 files) and Skips (7 total across 3 files)

| File | Pass | Fail | Skip | Root Cause Group |
| --- | ---: | ---: | ---: | --- |
| `tests/tier2/partiql/batchExecuteStatement.test.ts` | 24 | 1 | 0 | **G2 (BatchExecuteStatement `ReturnValuesOnConditionCheckFailure`)**: `ALL_OLD` on a batch member when no prior item exists returns an empty `Item: {}` instead of omitting `Item`. |
| `tests/tier2/partiql/fromFirstUpdate.test.ts` | 0 | 11 | 0 | **G3 (PartiQL `UPDATE ... FROM ... WHERE ... SET/REMOVE` / `FROM <table> WHERE ... SET/REMOVE`)**: PartiQL `FROM`-first update syntax (`FROM table WHERE ... SET ...`) is not recognized by `DynamoDbPartiQLParser`. |
| `tests/tier2/partiql/grammar.test.ts` | 15 | 4 | 0 | **G4 (PartiQL number grammar & exponent bounds)**: Leading-dot decimals (`.5`, `-.5`) in PartiQL `WHERE`/`INSERT` and 32-bit integer exponent overflow checks in PartiQL literals. |
| `tests/tier2/partiql/whereFunctions.test.ts` | 23 | 1 | 0 | **G5 (PartiQL `IS <type>` in `WHERE`)**: `attr IS STRING` / `IS NUMBER` / etc. in PartiQL `WHERE` filter expressions. |
| `tests/tier2/partiql/whereOverlap.test.ts` | 0 | 7 | 0 | **G6 (PartiQL `OR` branches with negated sort-key conditions)**: Key-plan extraction rejects `OR` branches combining a negated sort-key condition (`NOT sk = ...`, `sk <> ...`, `NOT begins_with(...)`, `NOT sk IN ...`, `NOT sk > ...`) with an equality branch. |
| `tests/tier2/vectorSearch/consumedCapacity.test.ts` | 14 | 4 | 0 | **G7 (Vector Search billed byte size vs 400 KB item size gate)**: Vector attributes are billed at `4 * dimensions` bytes for `PutItem`/`GetItem` `ConsumedCapacity`, while the 400 KB item size limit still sizes the `L`/`N` attribute by its stored numbers. |
| `tests/tier2/account/limits.test.ts` | 0 | 0 | 2 | Cloud-only / unimplemented (`DescribeLimits`, `DescribeEndpoints`). |
| `tests/tier2/backups/onDemand.test.ts` | 0 | 0 | 3 | Cloud-only / unimplemented (`CreateBackup`, `RestoreTableFromBackup`, `DescribeBackup`). |
| `tests/tier2/contributorInsights/basic.test.ts` | 0 | 0 | 2 | Cloud-only / unimplemented (`DescribeContributorInsights`, `UpdateContributorInsights`). |

### 2.3 Tier 3 Failures (213 total across 24 files)

| Subcategory / File | Pass | Fail | Root Cause Group |
| --- | ---: | ---: | --- |
| `tests/tier3/limits/numberExponent.test.ts` | 3 | 12 | **G8 (Number 32-bit exponent overflow/underflow before scale)**: Exponents outside 32-bit signed `int` range (or zero with an out-of-range exponent) must report `ValidationException` overflow/underflow across all item/key/expression surfaces. |
| `tests/tier3/limits/nestingDepth.test.ts` | 15 | 4 | **G9 (Document nesting depth > 32 error envelope & key naming)**: Stored attributes vs `ExpressionAttributeValues` (including in `TransactWriteItems` `ConditionCheck`) have distinct error message formats and key attribution. |
| `tests/tier3/limits/itemSizeBySurface.test.ts` | 27 | 14 | **G10 (`UpdateItem` per-clause write-size formula & pre-condition check, `AttributeUpdates` 400 KB cap)**: `UpdateItem` sizes the statement by its clause charge formula before evaluating `ConditionExpression`; `AttributeUpdates` caps finished item at 409,600 bytes. |
| `tests/tier3/error-messages/partiqlGrammar.test.ts` | 11 | 48 | **G11 (PartiQL grammar error messages)**: Exact AWS PartiQL parser diagnostics for empty `WHERE`, trailing `AND`/`OR`, table aliases (`SELECT`/`UPDATE`/`DELETE`/`INSERT`), `IS <type>` errors, number literal overflow/underflow/precision, and `FROM`-first update syntax errors. |
| `tests/tier3/error-messages/partiqlParameterNumbers.test.ts` | 27 | 22 | **G12 (PartiQL parameter number conversion/underflow/nesting order)**: Parameter numbers with exponents exceeding 32-bit int vs `1e2147483647` scale underflow, empty number strings, and validation ordering relative to nesting depth. |
| `tests/tier3/error-messages/partiqlFunctions.test.ts` | 13 | 13 | **G13 (PartiQL `attribute_exists` & `set_add` error messages)**: Exact error messages for invalid argument types/counts/positions in `attribute_exists` and `set_add` and reserved word `inner` in paths. |
| `tests/tier3/error-messages/partiqlMultiAttributeIndex.test.ts` | 0 | 8 | **G14 (PartiQL multi-attribute GSI `SELECT` refusal & ordering)**: Refusal message and validation precedence when running PartiQL `SELECT` against a multi-attribute composite GSI. |
| `tests/tier3/error-messages/valueLayers.test.ts` | 0 | 20 | **G15 (Number & duplicate set validation layers by operation)**: Envelope vs bare error messages for malformed numbers and duplicate set members across `PutItem`, `UpdateItem`, `DeleteItem`, `GetItem`, `BatchGetItem`, `BatchWriteItem`, `Query`, `TransactWriteItems`, `TransactGetItems`. |
| `tests/tier3/error-messages/transactWriteItems.test.ts` | 22 | 14 | **G16 (`TransactWriteItems` top-level validation messages)**: Table name format, empty/malformed `ConditionExpression`, unused `ExpressionAttributeValues`, and empty action item errors. |
| `tests/tier3/error-messages/transactGetItems.test.ts` | 5 | 2 | **G17 (`TransactGetItems` malformed table name member position)**: Top-level member constraint message naming `transactItems.<N>.member.get.tableName`. |
| `tests/tier3/error-messages/deleteItem.test.ts` | 5 | 4 | **G18 (`DeleteItem` validation envelope)**: Envelope messages for unused/empty `ExpressionAttributeValues`/`Names`, empty `ConditionExpression`, and invalid `ReturnValues`. |
| `tests/tier3/error-messages/putItem.test.ts` | 18 | 2 | **G19 (`PutItem` validation envelope)**: Envelope messages for unused `ExpressionAttributeValues`/`Names` and empty `ConditionExpression`. |
| `tests/tier3/error-messages/updateItem.test.ts` | 9 | 1 | **G20 (`UpdateItem` undefined condition value message)**: Exact error string when `ConditionExpression` references an undefined value alongside a defined one. |
| `tests/tier3/error-messages/batchGetItem.test.ts` | 11 | 2 | **G21 (`BatchGetItem` map-key & minimum-length constraint messages)**. |
| `tests/tier3/error-messages/batchWriteItem.test.ts` | 11 | 2 | **G22 (`BatchWriteItem` map-key & empty request list constraint messages)**. |
| `tests/tier3/error-messages/queryKeyConditionOperands.test.ts` | 5 | 5 | **G23 (`Query` `KeyConditionExpression` operand & multi-condition messages)**. |
| `tests/tier3/error-messages/searchVectors.test.ts` | 7 | 2 | **G24 (`SearchVectors` explicit null SerializationException & missing TableName ordering)**. |
| `tests/tier3/validation-ordering/batchOperations.test.ts` | 5 | 7 | **G25 (Batch enum & table-name validation envelope ordering)**. |
| `tests/tier3/validation-ordering/createTable.test.ts` | 7 | 2 | **G26 (`CreateTable` GSI vs LSI index-name error ordering)**. |
| `tests/tier3/validation-ordering/deepRequests.test.ts` | 7 | 14 | **G27 (Deep request nesting depth vs envelope/action validation ordering)**. |
| `tests/tier3/validation-ordering/query.test.ts` | 3 | 1 | **G28 (`Query` `ConditionalOperator` XOR vs `Select` enum ordering)**. |
| `tests/tier3/validation-ordering/queryKeyCondition.test.ts` | 4 | 2 | **G29 (`Query` `KeyConditionExpression` operand error vs undefined value ordering)**. |
| `tests/tier3/validation-ordering/transactions.test.ts` | 7 | 9 | **G30 (`TransactWriteItems` validation ordering across enums, token length, conditions, and missing tables)**. |
| `tests/tier3/validation-ordering/updateItem.test.ts` | 3 | 3 | **G31 (`UpdateItem` member envelope errors joined with missing `Key`)**. |
