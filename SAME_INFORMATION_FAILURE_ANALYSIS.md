# V4 Same-Information Failure Analysis

## Scope and immutable status

This is a report-only reviewer aid derived from the frozen V4 locked-test
evidence. It does not modify the raw evidence, preregistration, protocol status,
artifact index, published hashes, primary classification, or same-test rerun
policy.

The V4 result remains:

- protocol status: `failed_test_visible_frozen`;
- primary result classification: `invalid_method_identity`;
- statistical classification: `strongly_material`;
- same-test rerun permitted: `false`;
- confirmatory PVA performance claim permitted: `false`.

## Finding

The preregistered same-information audit evaluated 42,072 aligned primary
cycles. Five cycles failed, or 0.011884% of the audited cycles. All five
failures were limited to the composite `event_flags` field. For those cycles:

- configuration identity passed;
- every other field compared by the frozen same-information audit passed;
- target-component construction checks embedded in that audit passed;
- direct-method purity remained 1.0;
- the only differing token was `deadline_miss`.

The five rows are preserved in
[`same_information_failures.csv`](same_information_failures.csv):

| Trajectory | k | P flags | PV flags | PVA flags |
| --- | ---: | --- | --- | --- |
| `piecewise_constant_jerk__v4__test__001` | 154 | empty | empty | `deadline_miss` |
| `piecewise_constant_jerk__v4__test__003` | 86 | `deadline_miss` | empty | empty |
| `rapid_reversal__v4__test__015` | 215 | empty | `deadline_miss` | empty |
| `stationary_endpoint__v4__test__013` | 254 | `deadline_miss` | empty | empty |
| `stationary_endpoint__v4__test__014` | 260 | empty | `deadline_miss` | empty |

No failed row contained a differing exogenous-input, estimator-state,
feedback-correction, command-divergence, plant-saturation, state-reset, or
invalid-input token.

## Why the composite field failed

The frozen implementation includes `event_flags` in the fields that must match
across P, PV, and PVA. The pipeline constructs this composite field from tokens
with different causal roles:

| Token group | Examples in the frozen implementation | Audit role |
| --- | --- | --- |
| Exogenous input or input validity | `dropped`, `burst_drop`, `held`, `duplicate`, `timestamp_regression`, `outlier`, `nonfinite`, `impossible_jump`, `invalid_input` | Upstream information |
| Estimator state | `state_reset` | Estimator outcome/state |
| Execution | `feedback_correction`, `command_measured_divergence` | Method-execution outcome |
| Runtime | `deadline_miss` | Timing outcome |
| Plant | `plant_saturated` | Plant outcome |

At least `deadline_miss` is produced by method execution and timing, after the
cycle begins. It is not an exogenous estimator or predictor input. The observed
five failures therefore support this precise description:

> The preregistered same-information audit failed on five composite
> event-flag entries, although shared configuration identity, all other
> compared fields, and direct-method purity checks passed.

They do not support the broader claim that the methods were shown to receive
different estimator or predictor information.

## Interpretation boundary

This narrower diagnosis does not retroactively change the frozen gate. The
preregistered implementation treated `event_flags` as a same-information
field, so any difference makes the V4 confirmation non-confirmatory. The five
rows cannot be removed, reclassified, or used to restore a confirmatory
performance claim.

The token diagnosis also agrees with the independent runtime result: the
current instrumented Python pipeline failed the preregistered runtime gate.
That result establishes that this implementation did not meet the locked
hard-runtime criteria. It does not establish that an isolated or compiled
implementation of the algorithm can never meet a 100 Hz deadline.

## Frozen sources and deterministic extraction

The reviewer aid was derived by:

1. filtering the canonical same-information audit to `audit_passed == false`;
2. joining those five cycle keys to the three primary rows in the frozen
   locked-test `samples.parquet`;
3. preserving each method's `event_flags` value;
4. tokenizing the semicolon-delimited values and recording their differences;
5. setting `all_non_event_shared_fields_passed=true` only because each
   canonical `failed_fields` value contains no failure other than
   method-specific `event_flags`.

Frozen source identities:

- confirmation source commit:
  `461fc560461b0a4726cbabdb97b2dbd4dc305e0a`;
- locked-test `samples.parquet` SHA-256:
  `42ef10e9ced276c071cd26af3ac3cec4e43c92387676459808052e67bb59c826`;
- locked-test `same_information_audit.csv` SHA-256:
  `adebf77d9cb05eaf5333a15843661c3fe3c067eecff46aa5f65d87c2ce4cfa83`;
- root statistical copy SHA-256:
  `ec46bf920912179020ceaa0eeedcfe447026a5ec1a341b82ef71b83d99ba1a8e`.

Another confirmation using a revised audit definition requires a fresh V5
protocol and test set. V4 remains permanently frozen.
