# Ordered temporal prefix state acceptance (#41)

On 2026-11-16, all five fixtures in [ordered-state-manifest.json](ordered-state-manifest.json) passed the strict real-Kusto harness on engine `2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, with `max_memory_consumption_per_query_per_node=12884901888`. Each returned HTTP 200, one empty multiset diagnostic, and successful uncancelled completion without engine-error frames. Exact generated queries, frozen hashes, raw responses, engine version, and elapsed correctness-run samples are preserved in [ordered-state evidence](runtime-evidence/ordered-state/results.json).

A sixth [native real-group fixture](ordered-real-groups-manifest.json) also passes, preserving NaN, positive/negative infinity, signed zero, and finite grouping identity. The pre-fix scan lost exactly the NaN completion because scalar `NaN != NaN` triggered a reset; the type-aware comparison now agrees with an independent native `summarize` oracle. Both [the failing regression proof](runtime-evidence/ordered-real-groups-regression/results.json) and [the corrected successful execution](runtime-evidence/ordered-real-groups/results.json) are preserved.

The generated single-step scan batches equal timestamps and reads qualified preceding-timestamp state for every assignment. Literal cases cover viable earlier prefixes after intervening first steps, repeated references, simultaneous memberships, group resets, ungrouped input, strict ties/reversals, exact and one-tick-outside total spans, multiple completions, aliases, and multiple source searches. These engine results establish assignment semantics and result correctness for the recorded hashes. Ordered contributor enumeration remains unchanged until #47; full integrated acceptance and representative before/after performance remain #49 work.

The detection path has one membership aggregation, one sort, and one scan with one datetime state per proper sequence prefix plus a completion flag. It performs no prefix joins and creates no full sequence tuples. A one-position sequence reduces to distinct group/timestamp rows. Correctness-run elapsed samples are not performance comparisons.

# Native scalar source/type acceptance (#37 and refreshed #36)

Executed on 2026-11-16 with the existing strict runtime harness against engine `2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, and the query memory option `max_memory_consumption_per_query_per_node=12884901888`. All eleven generated queries returned HTTP 200, exactly one empty primary multiset/schema diagnostic, and successful uncancelled completion without engine errors. The SecurityEvent fixture also compares the complete public schema: datetime window bounds, native string Actor, and long ValueCount.

The selected fixtures cover direct grouped and ungrouped sources, duplicate source matches, standard mappings, retained scalar context, native long/string grouping, guid alias chains, mixed direct/child sources, native sum/percentile output, and real/long/decimal distinct-value precision. Their expected rows are independent literal examples; this is a scoped correctness gate, not a full-corpus or performance result. Other regenerated hashes still need integrated acceptance. Historical results below apply only to their original recorded hashes.

Exact executed queries, hashes, raw completion/error/resource frames, engine version, and harness results are preserved under [native-scalars](runtime-evidence/native-scalars/results.json) and [source-predicates-native](runtime-evidence/source-predicates-native/results.json).

| Fixture | Executed SHA-256 | Result |
| --- | --- | --- |
| `event_count_duplicate_sources.kql` | `ce4be2e5c144899fb02a64e5bb6ba3de36d2c20dd3f7b6c0e2225cef240fa189` | PASS |
| `event_count_grouped.kql` | `8ba348aeca8bd59c6dd74302739c63e6c79d7b95649a24bf142e5c34fcd5d37d` | PASS |
| `standard_mappings.kql` | `df28cd7a67dbd37e8856d006feb3776b9b76940d6b44b52ed2c68d363fb2749d` | PASS |
| `typed_grouping_event_count.kql` | `a92e409ee0ba26aaed07578e84991a9f72cbf1631a249ee57e57a8ad13866f19` | PASS |
| `typed_grouping_extended_chain.kql` | `e313ca053afeae46a9d0d3ee29e615ba9eac14570892e9aaeea70f3c1885e876` | PASS |
| `typed_grouping_mixed_chain.kql` | `9facfe2cc77005e26f568262a185ac7df84c93752bee2e4c35fbcba039377e54` | PASS |
| `typed_grouping_value_count.kql` | `c340c283132f66d9faa018ebb4b5a976f96fc0970bc46503aed8205d43309e78` | PASS |
| `typed_grouping_value_percentile.kql` | `05cf0b029b94ec95851a36927ed09b00d8c05ce904703d8b0312c1e1bf2a958f` | PASS |
| `typed_grouping_value_sum.kql` | `2565a30982f04f7d02af5f6f54401a7e48f1c30cd711c02856c499f5b3430554` | PASS |
| `value_count_typed_precision.kql` | `8139cb1f1fcba90338d2508f581b703626d5684d1da06e2f5804005db46c907d` | PASS |
| `source_predicates_securityevent.kql` | `120494d80e695613471489b7effb949b069b43c6bd69ac003034d809ce010c10` | PASS |

# Numeric sum runtime evidence

Executed on 2026-11-15 against the Microsoft Azure Data Explorer Kusto emulator,
database `NetDefaultDB`, through the Kusto REST query API. All searches were
generated by standard Sigma collection parsing and `KustoBackend.convert()`;
expected rows are independent literal examples in `generate.py`.

- Engine: `2026.11.15.0741-2645-8e3387b-master`.
- Image: `mcr.microsoft.com/azuredataexplorer/kustainer-linux:latest`.
- Image digest: `sha256:3a66bd2276e683ce4a7dbadf4aea4993c1847796f2db244d6c75335e8db72bd5`.

| Fixture | Observed outcome |
| --- | --- |
| `value_sum_comparisons.kql` | Zero multiset differences for all six comparisons and a fractional threshold. |
| `value_sum_chained.kql` | Zero multiset differences for mixed child summaries and direct measurements. |
| `value_sum_reals.kql` | Zero multiset differences for mixed integer/real sums and excluded nonfinite/nonnumeric values. |
| `value_sum_integer_bounds.kql` | Zero multiset differences at the signed-long maximum, including cancellation. |
| `value_sum_overflow.kql` | Failed as required with `SEM0080` and `Kusto value_sum integer overflow`. |
| `value_sum_negative_overflow.kql` | Failed as required with the same explicit error below the signed-long minimum. |
| `value_sum_duplicate_sources.kql` | Zero multiset differences; all four matching searches and repeated observations contribute. |

The first execution exposed two real engine behaviors: `gettype()` reports real
values as `double`, and native long `sumif()` silently wraps on overflow. The
delivered queries accept `double`, accumulate integers as decimal, then assert
that every anchored integer-only sum fits signed 64-bit before applying the rule
comparison. This range check also catches windows whose overflow would otherwise
make the comparison false. Fractional comparisons do not coerce large integer
totals to floating point. Windows containing real observations use floating-point
addition and direct comparisons without a tolerance.

The sum summaries use `materialize()` so the global overflow check and output
consume the same summaries. Its shared 5 GB per-node cache limit fails explicitly
on exhaustion. These small deterministic fixtures establish result correctness,
not throughput or production capacity. Other fixture families require their own
recorded results; the pytest fixture-consistency check does not execute KQL.

# Numeric average runtime evidence

Executed on 2026-11-15 against the same Microsoft Kusto emulator engine
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, via the REST
query API. Queries are generated from standard Sigma collection parsing and
`KustoBackend.convert()`; `average.py` provides independently worked expected
rows and the checks compare full result multisets.

| Fixture | Observed outcome |
| --- | --- |
| `value_avg_comparisons.kql` | Zero differences for all six operators, fractional thresholds, exact windows and undefined populations. |
| `value_avg_reals.kql` | Zero differences for finite numeric observations, nonfinite exclusion, undefined `neq`, and direct floating-point comparison. |
| `value_avg_duplicate_sources.kql` | Zero differences with unequal source/search multiplicity. |
| `value_avg_mapped.kql` | Zero differences for per-source aliases and mappings over two tables. |
| `value_avg_chained.kql` | Zero differences for average children plus a direct source; each child metric contributes once. |
| `value_avg_integer_bounds.kql` | Zero differences at signed-long maximum and for cancellation; results follow real precision. |

The initial engine run exposed that `avgif()` with no qualifying observations
returns NaN, which passes `neq`. Generated queries now count qualifying finite
observations and require a positive count before applying every comparison. The
corrected queries passed all six fixtures. Integer observations are converted to
real before averaging, avoiding integer-sum wrap while accepting ordinary
floating-point precision. These checks establish fixture correctness, not
production throughput or capacity.

## Boolean temporal runtime evidence

Executed on 2026-11-15 against the same Microsoft Kusto emulator, database
`NetDefaultDB`, engine `2026.11.15.0741-2645-8e3387b-master`, through the REST query
API. These five files were generated through ordinary collection parsing and
`KustoBackend.convert`; their literal expected summaries are independently worked
examples in `boolean_generate.py`. Every execution completed successfully and
returned zero multiset differences (including duplicate-summary detection).

| Fixture | Verified behavior |
| --- | --- |
| `boolean_presence.kql` | AND/NOT, repeated matches, independent groups, simultaneous disqualification, inclusive endpoint and one tick outside, no expiry-only results. |
| `boolean_alternatives.kql` | C alone satisfies `(A and B) or C`; incomplete and successful alternatives, total span, overlapping summaries. |
| `boolean_precedence.kql` | NOT/AND before OR, evaluation on a negated source timestamp, absent and present negative evidence. |
| `boolean_nested_not.kql` | Nested negation, B alone, inclusive boundary and one tick outside. |
| `boolean_chained.kql` | Mixed direct and child sources, child WindowEnd anchoring, negation at the exact endpoint and one tick outside. |

The fixtures establish the delivered presence behavior. Retained context from
satisfied alternatives is deferred to its separate stage. This run makes no
throughput claim.

## Exact value-count runtime repair

Executed 2026-11-15 against the same Microsoft Kusto emulator and database
`NetDefaultDB`, through `/v2/rest/query`. All seven value-count fixtures and all
thirteen other fixtures containing chained sources completed successfully with
zero multiset differences. This includes grouped and ungrouped comparisons,
multiple independent counted fields, missing-value anchors, typed values, and
mixed/multilevel chains across numeric and temporal types.

The typed fixture additionally checks forty nested array levels, datetime/string
positional swaps in mixed arrays, and array order. Reordered nested bags, repeated
real NaNs, signed zero, empty containers, and nested datetime/timespan/GUID values
versus their text forms retain their existing independent expected summaries.

The prior `dynamic == dynamic` engine error is repaired with iterative traversal
and typed scalar leaf comparisons. Bag keys are captured once per traversal
frame. JSON encodes bag-key path segments and bounds traversal work, but does not
identify values. A global range assertion and terminal completion assertion abort
on traversal limits rather than returning partial identities. Materialization and
quadratic pair joins can still fail engine resource limits; no production
throughput or memory-capacity claim is made.

## Exact nearest-rank percentile — ticket #20

Executed 2026-11-15 through the emulator's `/v1/rest/query` endpoint against
`NetDefaultDB`, engine `2026.11.15.0741-2645-8e3387b-master`, using the image
and digest recorded above. Every delivered percentile fixture completed
successfully with zero primary-result rows (zero multiset differences):

| Fixture | Observed outcome |
| --- | --- |
| `value_percentile_comparisons.kql` | All six comparisons, fractional threshold, median 2 for `[1,2,100,200]`, duplicates, zero, finite-input exclusions, undefined windows including `neq`, inclusive boundaries, overlaps and independent groups. |
| `value_percentile_endpoints.kql` | Selectors 0, 50 and 100 select exact adjacent signed-long maxima and finite double extremes ±1e300. |
| `value_percentile_population.kql` | Population 101 selects values 1, 2, 51, 100 and 101 for selectors 0, 1, 50, 99 and 100. |
| `value_percentile_aliases.kql` | Group/measured aliases across tables and repeated source searches preserve multiplicity: medians 1 then 100. |
| `value_percentile_chained.kql` | Percentile → sum → percentile returns 2 then 102, consuming scalar child metrics once at their `WindowEnd`. |

Initial fixture execution exposed a duplicate `let Events` binding and the
engine's lowercase exponent rendering for dynamic doubles. The fixture bindings
and literal expected strings were corrected, and both affected fixtures reran
successfully. No production query correction was needed from these executions.
The rank query sorts all observations without a capped collection. It uses a
real primary sort key and decimal tie-break to retain ordering of adjacent long
values that share a double representation; selected values retain their type.
Materialization uses the engine's shared 5 GB per-node cache and can fail
explicitly on exhaustion. These deterministic result checks do not establish
throughput or production memory capacity.

## Retained aggregate and ordinary temporal context (#22)

Executed 2026-11-15 on the same Kusto emulator engine
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, through `/v2/rest/query`.
The complete queries are generated through standard Sigma collection parsing and
`KustoBackend.convert`; `context.py` supplies independently worked expected rows.

| Fixture | Outcome |
| --- | --- |
| `context_event_count.kql` | Zero exact multiset differences; metric 3 and two distinct typed User values. |
| `context_value_count.kql` | Zero differences; distinct measured-value metric 2. |
| `context_value_sum.kql` | Zero differences; repeated measurements preserve sum 40. |
| `context_value_avg.kql` | Zero differences; repeated measurements preserve average 40/3. |
| `context_value_percentile.kql` | Zero differences; exact nearest-rank result 10. |
| `context_temporal.kql` | Zero differences; ordinary temporal RuleCount remains 1. |
| `context_nested.kql` | Zero differences; five distinct typed contexts from eight matches, observed tuple membership preserved. |

Every scalar fixture verifies source/correlation request union, duplicate declaration
and value removal, scalar group output, typed numeric/string identity, and observed
field relationships. The nested fixture additionally checks reordered bags,
datetime/string array positions, NaN, and signed zero. The first live probe revealed
that Kusto `make_list` flattens array inputs one level: an extra array box is required
to preserve both nested displayed values and the observed relationship tuples.
Collection lengths are checked against complete counts and fail explicitly on the
engine collection limit rather than silently truncating. Typed traversal uses the
shared exact identity helper and its explicit completion/range guards. These tiny
fixtures establish behavior, not throughput. Ordered/Boolean context membership
and individual-value consumption by parents are separate follow-up slices.

## Boolean retained context (#24)

Executed 2026-11-15 against Kusto emulator engine
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, via `/v2/rest/query`.
All six complete queries were generated through standard Sigma collection parsing
and `KustoBackend.convert`. Each returned zero exact summary multiset differences
against literal worked expectations in `boolean_context.py`:

| Fixture | Verified outcome |
| --- | --- |
| `context_boolean_alternatives.kql` | Failed A-and-B alternative supplies no values; simultaneous successful alternatives retain A, B, and C, including numeric/string typed distinction. |
| `context_boolean_absence.kql` | Absence supplies no values; an absence-only successful alternative retains the original summary with empty arrays. |
| `context_boolean_nested.kql` | Nested negation supplies B and C while excluding A. |
| `context_boolean_shared.kql` | Shared A source contributes only its observed tuple alongside C. |
| `context_boolean_precedence.kql` | Parser precedence excludes the failed B-and-not-C alternative. |
| `context_boolean_child.kql` | Positive child and detection sources both supply their observed scalar field relationships. |

The comparisons check summary membership, RuleCount, displayed collection sizes,
and typed observed tuples. Tuple strings are sorted only for fixture comparison;
production typed identity does not use serialization. The first fixture probes
exposed unordered tuple comparison and a hard-coded child hidden-column name;
fixtures now canonicalize tuple order and exercise scalar child grouping fields.
Consuming individual retained child values remains a separate delivery slice.
The metric/evaluation query is unchanged by the context request; matched context
is filtered separately using window-wide presence and the parsed Boolean tree.
These deterministic examples establish behavior rather than throughput.

## Ordered participating context (#23)

Executed 2026-11-15 on Kusto emulator engine
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, through `/v2/rest/query`.
All four final generated queries completed successfully with empty exact multiset
difference tables:

- `context_ordered_alternatives.kql`: all valid starts and middle alternatives,
  duplicate source matches, strict ordering, failed whole-span and tied sequences,
  exact inclusive endpoints, and overlapping completions.
- `context_ordered_repeated.kql`: A → B → A, excluding nonparticipating tied matches
  while retaining every successful start and each later completion.
- `context_ordered_ungrouped.kql`: complete participating evidence without grouping.
- `context_ordered_aliases.kql`: per-source aliases preserve the same membership
  and observed User/Server relationships.

`ordered_context.py` supplies literal independently worked expected members at
completion times; comparison retains summary multiplicity. Detection-only prefix
pruning remains valid for finding completions, while separate evidence traversal
keeps every successful full alternative. It uses relational joins without bounded
sequence arrays or sampling; dense alternatives can exhaust engine resources and
no throughput claim is made.

# Standard pipeline mapping runtime evidence

Executed `standard_mappings.kql` on 2026-11-15 against the Microsoft Kusto
emulator `2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`.
The result contained zero exact multiset differences and no query errors.
`standard_mappings.py` generates its search through standard Sigma collection
parsing and `KustoBackend.convert()`. Independent literal expectations require
sum 40 from two process searches contributing 10 each and one network search
contributing 20, one logical Host group, and retained logical User values Alice
and Bob. The fixture exercises source-specific standard mappings of detection
predicates, timestamps, groups, measured fields, and context, with aliases and
escaped physical column names. An unmatched process row carrying 100 is excluded.

The first engine check exposed incorrect detection identifier quoting for names
containing spaces. Detection fields now use bracketed JSON string identifiers,
matching the quoting already used for correlation fields.

After merging Boolean retained context, all eleven existing non-Boolean context
fixtures (seven aggregate/temporal/nested and four ordered) were rerun together
against the same engine and returned empty exact summary multiset difference tables.

## Dependency order and diagnostics (#30)

The execution below records initial revision `79bdc75`. These fixtures were
regenerated after integration with typed grouping and shared-child validation.
Execution of that regenerated revision is pending the frozen integration runtime
pass; the earlier result is not evidence for the updated KQL.

Executed 2026-11-15 against local Kusto emulator `NetDefaultDB` through
`/v2/rest/query`. A fresh `.show version` reported engine product version
`2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`.
Both `dependency_order_suppressed.kql` and `dependency_order_generate.kql`
completed without engine errors and returned zero exact summary multiset
differences against the literal independently worked rows in
`dependency_order.py`.

The deliberately reordered collection shares a child across consumers, chains
three correlation levels, mixes child and raw network sources, and resolves both
names and UUIDs. Each child contributes once at its WindowEnd; the exact endpoint
at 12:06 keeps the 12:01 child available, and the 12:07 direct match retains its
12:02 endpoint. The unrelated detection contributes nothing to the parent.
Generation controls preserve these parent results. Public conversion tests also
compare all 24 permutations of the four correlation documents with detections
interleaved, for both generation settings; output query order is not a dependency
contract. Diagnostics tests cover invalid method/configuration, unavailable or
malformed source conversion state, invalid search/table types, child schema/time
metadata, and unresolved references. Existing partial ordered threshold rejections
remain enforced. The examples establish correctness, with no throughput claim.
# Count parents consuming retained context

Executed on 2026-11-15 against Microsoft Kusto emulator engine
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, via REST.
`count_context.py` generates these queries through standard Sigma collection
parsing and `KustoBackend.convert`; its expected rows are independent literal
examples. Each check compares full result multisets and requires zero differences.

| Fixture | Observed outcome |
| --- | --- |
| `count_context_pairs.kql` | Zero differences: only Alice/server1 and Bob/server2; duplicate retained pairs count each child once per group. |
| `count_context_mixed.kql` | Zero differences: direct and child sources combine with their established contribution counts. |
| `count_context_ungrouped.kql` | Zero differences: two child summaries contribute two events despite repeated/multiple context values. |
| `count_context_values.kql` | Zero differences: ungrouped distinct retained users are Alice and Bob. |
| `count_context_restricted.kql` | Zero differences: users are restricted to their observed Server group. |
| `count_context_typed.kql` | Zero differences: numeric 1 and textual "1" return separate typed groups, each counting the child once. |

The initial 4 GiB emulator exhausted memory while processing grouped consumers;
Kusto query history reported `System.OutOfMemoryException`, surfaced by REST as
an internal service error. The final fixtures ran with the emulator memory limit
increased to 12 GiB. Group and counted-value identities share one exact typed
identity traversal, preventing an unnecessary nested identity stage. An earlier
grouped value-count query with separate identity stages exceeded a 120-second
client timeout. These small examples demonstrate correctness under the recorded
configuration; they establish neither a production capacity target nor a minimum
memory guarantee. Resource exhaustion fails explicitly and does not truncate
retained values or invent relationships.

After integrating count parents consuming retained context, the additional
`standard_mappings_parent.kql` fixture completed on the same 12 GiB emulator with
zero exact multiset differences and `HasErrors: false`. Its value-sum child
retains mapped Alice/Bob values under logical User; the event-count parent groups
by logical Host and User and returns each user once, without reapplying physical
detection mappings to the child. String projections in the fixture comparison
align the engine's dynamic grouping columns with the literal expected strings.

### Ordered typed-leaf signature candidate (2026-11-15)

The identity implementation now serializes sorted typed scalar leaves and empty
containers into full signatures, without hashes. One summary checks traversal
completion, range capacity and the independent leaf count; an ordered scan
assigns representative IDs without a leaf-pair or canonical self-join.

On the same emulator engine and 12 GiB configuration, all 31 prepared fixtures
in `context_*.kql`, `count_context_*.kql` and `value_count_*.kql` returned empty
primary results with `HasErrors=false`. This includes all 17 retained-context
fixtures, six count-context consumer fixtures, seven existing value-count
fixtures and the new `value_count_typed_precision.kql`. The latter verifies
adjacent doubles and maximum longs remain distinct, equivalent decimal scales
and signed zeros coalesce, NaNs coalesce while positive/negative infinity remain
distinct, and nested extended values survive reordered bags and 65 array levels.

A separate 20-case typed identity comparison probe also passed, covering extended
scalar/string distinctions, adversarial/escaped key paths, array order/length,
empty containers, nulls, NaN and precision boundaries. Its recorded query resource
report was 0.4518878 seconds and 9,431,312 bytes peak memory.

This helper change alone does **not** repair the larger three-level numeric
retained-context planning failure: rewriting its seven identity helpers retained
the literal expected sum 130, but the initial canonical-join candidate exhausted
memory and the fused-summary/scan candidate exceeded the 180-second client
limit without a result. At that revision the required chain was unvalidated;
the additional repair and successful execution are recorded below. No throughput
or capacity claim follows from these small successful fixtures.

### Flat validation and numeric retained-context chain (2026-11-15)

The required three-level numeric retained-context fixture now passes on the same
engine `2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, with a
12 GiB emulator container. `numeric_context_nested.kql` returns an empty exact
multiset difference and `HasErrors=false`: an intermediate sum 140 retains
amounts 10, 20 and 100, which contribute sum 130 to its parent. This confirms
retained values do not reconstruct descendant multiplicity.

The structured final query reported execution time 182.4468116 seconds,
91.886019 seconds of planning CPU and 21,462,032 bytes peak query memory.
No server timeout override was used. These reported query resources are not a
production capacity claim. A preceding flat-check prototype also passed the same
literal expected result, in 211.8873027 seconds.

Sharing materialized child summaries alone, and combining that sharing with the
typed-signature helper, still failed during planning. The remaining cause was
recursive validation: Kusto evaluates `assert` during analysis. The final query
embeds data-only child searches and carries independently scoped validation
bodies as flat metadata. One outer assertion checks every nonempty diagnostic;
its literal message lists the possible failure categories. Descendant checks
remain independent of ancestor qualification and requested context fields.
See Microsoft's [assert contract](https://learn.microsoft.com/en-us/kusto/query/assert-function).

The final flat candidate also passed four explicit-failure probes. Positive and
negative signed-long overflow raised an assertion containing
`Kusto value_sum integer overflow`. The prepared
`numeric_context_noqual_overflow.kql` raised the same category even though the
child requires sum zero and its event-count parent requires 999 summaries.
A diagnostic variant replacing the overflow diagnostic with an unknown nonempty
sentinel also failed, confirming validation does not depend on a known-message
whitelist. Successful empty result tables would fail these overflow checks.

This is the focused planning-repair acceptance record. The remaining numeric
fixture matrix and integrated feature gates are recorded separately after the
final branches are combined.

## Temporal retained-context consumers and multilevel requests (#27)

`temporal_context.py` supplies literal worked expected summaries, independently
of the generated query. Every fixture is generated through standard Sigma
collection parsing and `KustoBackend.convert`, then compares public result
columns as exact multisets. The three changed count-consumer fixtures are also
included because inherited requests now continue into their returned summaries.

| Fixture | Acceptance behavior |
| --- | --- |
| `temporal_context_unordered.kql` | Mixed child/direct identities at child WindowEnd; duplicate child evidence does not change RuleCount. |
| `temporal_context_ordered.kql` | Both later completions retain every participating child pair and every successful earlier direct alternative. |
| `temporal_context_boolean.kql` | Only C contributes Bob context when child-and-B fails; Alice retains the successful conjunction. |
| `temporal_context_multilevel.kql` | Unordered middle passes inherited requests to downstream User/Server groups without cross-pairs. |
| `temporal_context_ordered_multilevel.kql` | All participating ordered alternatives survive another consumer. |
| `temporal_context_boolean_multilevel.kql` | Failed conjunction evidence cannot leak into downstream groups. |
| `temporal_context_shared.kql` | Two branches sharing one child remain distinct immediate sources, with only observed pairs joining. |
| `count_context_restricted.kql`, `count_context_ungrouped.kql`, `count_context_values.kql` | Existing count metrics remain correct with inherited context now included in returned summaries. |

# Review regression runtime evidence

Executed on 2026-11-15 against the same Microsoft Kusto emulator, database
`NetDefaultDB`, with a 12 GiB container limit. The final queries come from
`empty_child_context.py` through standard Sigma parsing and public conversion.
Each accepted response contains exactly one `PrimaryResult` with zero multiset
difference rows and a final `DataSetCompletion` with `HasErrors: false`.

| Fixture | Observed outcome |
| --- | --- |
| `escaped_mixed_detection.kql` | Literal and wildcard branches both match the mapped column containing a space. |
| `empty_child_value_sum_ungrouped_eq.kql` | One zero-sum summary at the child window end, with empty Amount and relationship collections. |
| `empty_child_value_count_ungrouped_eq.kql` | One zero-distinct-count summary with the same empty retained context. |
| `empty_child_value_sum_Amount_eq.kql` | No summary: the retained grouping value is absent. |
| `empty_child_value_count_Amount_eq.kql` | No summary for the same missing retained group. |
| `empty_child_value_sum_ChildRuleCount_eq.kql` | Zero sum grouped by scalar child RuleCount, aliased as ChildRuleCount; empty retained context. Passed in 228.81 seconds. |
| `empty_child_value_count_ChildRuleCount_eq.kql` | Zero distinct count in the same scalar child group, with empty retained context. Passed in 154.46 seconds. |

The input contains an A-only row with Amount 42. The child expression
`not A or not B` succeeds through absence of B, so no positive match contributes
retained values. The child's scalar RuleCount is 1. Literal expected results
require zero measurements and zero retained values/relationships; a fabricated
null relationship would fail these checks.

A superseded alternate grouping both child and parent by Host failed after
160.32 seconds with an internal-service-error response; engine query history
confirmed `OutOfMemoryException` at the 12 GiB resource limit. It is not an
acceptance pass. The final scalar-metric fixtures keep the child ungrouped and
exercise scalar child grouping without that extra child grouping plan.

## Integrated matrix preparation (#32): PENDING

The seven-by-seven retained-context producer/consumer matrix and strict reusable
v2 runtime harness are prepared in `integrated.py` and `runtime.py`.
`acceptance-manifest.json` identifies the candidate fixtures by SHA-256. No
real-engine execution of this new matrix or combined acceptance candidate was
performed during preparation. Passing fixture-generation and harness-protocol
pytest checks is preparation evidence only. Follow the command and recording
contract in [README.md](README.md) after freezing the integrated candidate.

Ranges still require the released upstream parser dependency tracked in #31.
Partial ordered thresholds remain deferred and clearly rejected. Neither #32 nor
the parent full-coverage target can be marked complete from this preparation.

### Frozen combined candidate attempt: `87ad132` (2026-11-15)

The numeric acceptance batch was regenerated from frozen commit
`87ad13250296cb7f41baaeda55f9fed3c8845714`. Generated numeric queries exactly
matched the committed fixtures. The engine reported product version
`2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`, database
`NetDefaultDB`, with the emulator container held at 12 GiB.

Positive/negative overflow, `numeric_context_noqual_overflow.kql`, and an unknown
nonempty diagnostic probe all passed their expected-failure checks: each returned
`SEM0080` with the explicit correlation-validation assertion. These are failures
of the supplied invalid inputs, not successful empty result tables.

The **first positive fixture**, `numeric_context_value_sum.kql`, failed after
147.721 seconds with HTTP 520 and `Internal service error`. Its regenerated
query was 147,482 bytes, SHA-256
`67306f853cce7cdb4abed1221fe01e5f124c37e492f0feb1a602fbabb93bfa05`.
The engine activity ID was `a3bf93a2-9fad-4a5d-a4f1-00018f2fade9` and client
request ID `unspecified;f9683b5d-8e6c-4cab-b8af-14551bcbd681`.

The batch stopped immediately; the other twelve positive fixtures were not
executed on this candidate. Requests used default server properties and a
360-second client limit. This was not a reported configured timeout, so no
server-timeout retry was attempted. Local request/response hashes and outcomes
were retained under `/tmp/kusto-final-26` for diagnosis.

The positive numeric matrix is therefore **not runtime-complete** on this
combined revision. The earlier successful focused three-level query remains
evidence for its recorded earlier candidate, not a substitute for this gate.

# Frozen typed-grouping acceptance: partial, 2026-11-15

Ran the strict `runtime.py` harness against candidate `9325782` (frozen KQL
candidate `87ad132`) starting at `2026-11-15T17:39:10.762642+00:00`. All nine
owned fixture generators reproduced the committed bytes and frozen manifest
hashes before execution; no expected rows or source queries were changed.
Manifest SHA-256 was
`e484f73ec883733d3339d7695efb15564f7e6a99414723ff87006a6c1c749cda`.

The engine reported product version `2026.11.15.0741-2645-8e3387b-master`, build
`1.0.9815.14081`, database `NetDefaultDB`. The emulator stayed at 12 GiB;
`max_memory_consumption_per_query_per_node` was `12884901888` and the client
limit was 360 seconds. The batch ran serially with exclusive engine access and
stopped at the first failure.

| Fixture | SHA-256 | Strict result |
| --- | --- | --- |
| `typed_grouping_event_count.kql` | `616ae273b91b40cc83ad2c21c26612748066d10111fc4f8067f6715f62dd82ad` | PASS, HTTP 200, 2.548 seconds; one empty primary diagnostic table. |
| `typed_grouping_extended_chain.kql` | `7d2a7eaddf679ebff1728ccc5c8afcfabcd7f806aa72417cd29fdc2cb75ad61a` | FAIL, HTTP 200, 11.380 seconds; two difference rows. |

Both responses contained exactly one completion with `HasErrors=false` and
`Cancelled=false`. The extended-type fixture expected runtime type `real` for
its real-valued group, while Kusto returned `double`. Its exact difference was
`["2026-01-01T12:00:00Z","real",1,-1]` and
`["2026-01-01T12:00:00Z","double",1,1]`. This is a fixture type-label mismatch,
not an engine resource error or a successful empty diagnostic. The frozen
expected rows were left unchanged, and no retry was made.

The remaining seven fixtures were **not executed** in this batch:
`typed_grouping_mixed_chain.kql`, `typed_grouping_temporal.kql`,
`typed_grouping_temporal_ordered.kql`, `typed_grouping_value_avg.kql`,
`typed_grouping_value_count.kql`, `typed_grouping_value_percentile.kql`, and
`typed_grouping_value_sum.kql`. Typed grouping is therefore **not
runtime-complete** on this candidate.

The durable local runner record, version response, requests, and responses are
under `/tmp/kusto-final-29`. Response SHA-256 values were
`c91f12031ced95df361a752a72c37ec347ab05181c62e6a3cffc9e554464cee3`
(event count) and
`1faa2d4e4c7f6ec4b1860ab6b409d185e8605144bb5b9b72a32c5d2c9ca9821e`
(extended chain).


# Typed-grouping continuation: all nine owned fixtures passed

Continued the preceding frozen typed-grouping batch at
`2026-11-15T17:42:27.768048+00:00` after correcting exactly the expected runtime
**type label** `real` to Kusto's `double` in the extended-chain fixture.
Source values, types, group membership, and expected multiplicity were unchanged.
The other **159 fixture hashes were verified unchanged**. The regenerated
manifest SHA-256 is
`bc938f31dbb39f6038731538067a4332cea3e8593bd7c66dd2c07f94741a00e6`.
The initial failed response and its history remain recorded above.

The strict harness ran the corrected extended chain and the seven previously
unexecuted fixtures serially. Every continuation response had HTTP 200, exactly
one empty primary diagnostic table, and exactly one completion with
`HasErrors=false` and `Cancelled=false`. Together with the unchanged event-count
fixture's initial PASS, **all nine typed-grouping acceptance fixtures passed**.
This supersedes the preceding batch's incomplete typed-grouping status only;
it does not establish acceptance of the other spec #17 stages.

| Fixture | SHA-256 | Seconds | Result |
| --- | --- | --- | --- |
| `typed_grouping_extended_chain.kql` | `1ca1df81e490c52ba9812f3ad7edea856f02d26b63ba97f87534f277701c4beb` | 10.456 | PASS |
| `typed_grouping_mixed_chain.kql` | `3477779da7956ddaec0370a6c80e91fafa725ecbbc737967748a09f593fa0359` | 119.505 | PASS |
| `typed_grouping_temporal.kql` | `e842e58a5db9ce4ab94651cc045b6cb7fb25c1eb23245b1e1cb7a2963dee834e` | 1.052 | PASS |
| `typed_grouping_temporal_ordered.kql` | `83dc7309081074751fbc7696b7917818e4e8460775d1ef7f2b1718b6439f3373` | 1.008 | PASS |
| `typed_grouping_value_avg.kql` | `e9d50251dbafbc8b7faf278a0091511124ee6e25afcf1f66c83474de911ac112` | 0.985 | PASS |
| `typed_grouping_value_count.kql` | `5bfbc6bec9e76ac446ff900447b6f321e5299779bf066545bc84e1652fb83546` | 1.397 | PASS |
| `typed_grouping_value_percentile.kql` | `a20650cf85435ce2a3ff3694b8f8f6ea3001dca22570b1439b46df07a0dd56e6` | 1.595 | PASS |
| `typed_grouping_value_sum.kql` | `ef2e5d7fa7a28ec4ec6ae18fd7ac8e96719ec53bf288b0f9bf175bcaacc5788c` | 1.366 | PASS |

The engine and database were unchanged: product version
`2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`, `NetDefaultDB`.
The emulator stayed at 12 GiB with
`max_memory_consumption_per_query_per_node=12884901888` and a 360-second client
limit. No memory increase or resource-failure retry occurred. The 119.505-second
mixed-chain result establishes correctness for this example, not throughput.
The continuation's durable local version response, requests, responses, and
runner record are under `/tmp/kusto-final-29-corrected-escalated`. An initial
sandbox connection failure before any continuation query was retained under
`/tmp/kusto-final-29-corrected`; the authorized harness then ran with local
emulator access. Full configured pytest after the label correction passed all
585 tests with 96.15% coverage; Ruff and diff checks passed.

### Temporal/count frozen-candidate attempt: `9325782` (2026-11-15)

The seven `temporal_context_*.kql` fixtures and the changed
`count_context_restricted.kql`, `count_context_ungrouped.kql`, and
`count_context_values.kql` fixtures regenerated exactly and matched the frozen
`acceptance-manifest.json` hashes. Their KQL is unchanged from source freeze
`87ad132`. The selected batch ran in manifest order through the strict harness
from integration candidate `9325782`, using a 360-second client timeout and
`max_memory_consumption_per_query_per_node: 12884901888` (12 GiB). The emulator
container remained at 12 GiB; no memory increase or retry was performed.

The management response reported engine product version
`2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`, database
`NetDefaultDB`. The frozen manifest SHA-256 was
`e484f73ec883733d3339d7695efb15564f7e6a99414723ff87006a6c1c749cda`.

The first fixture, **`count_context_restricted.kql`**, failed after **152.232
seconds** with HTTP **520** and `Internal service error`. Its 165,115-byte query
had SHA-256
`f29816be0af4aa777090bda0e691ba3807a0599fa13cc7795083a5ba21d0c468`.
The response carried activity ID `045586f9-53cf-4bbe-a1d8-3f9372b92f68` and
client request ID `unspecified;2a2a3714-0ab8-4613-8ca0-dcd0cccba4e9`.
The response alone does not identify the underlying engine cause.

The harness rejected the non-v2 error response and stopped immediately. The
remaining two count fixtures and all seven temporal fixtures were **not run**.
There were no acceptance passes in this batch, and the temporal stage remains
**runtime-incomplete** on the combined candidate. Earlier focused results do not
substitute for this failed gate.

[Structured evidence](runtime-evidence/temporal-9325782.json) records all ten
selected hashes and their attempted/not-run states, request options, engine
version, strict harness outcome, error payload, and saved-artifact hashes. Raw
local requests and responses remain under
`/tmp/kusto-ticket-27-final-acceptance-9325782-network`.

### Focused numeric fixture preparation: seven minimal cases (offline)

The three simple ungrouped numeric parents, their three empty-population cases,
and the adjacent-long boundary case now request only `Amount` and use an
ungrouped child. Those literal inputs have one relevant Host/User combination;
unused User context and child Host grouping do not affect the agreed numeric
observations. Every literal input, threshold, expected numeric value, and result
comparison is unchanged. The cases still require 10,10,20 to contribute sum 30,
average 15 and percentile(60) 20, preserve zero/undefined empty behavior, and
select the exact adjacent long at the boundary.

This is fixture preparation only: no engine queries were run for this reduction.
Grouped/mixed inputs, scalar metrics, multilevel retention and multiple-child
fixtures were left intact. Their earlier resource failures and unexecuted gates
remain recorded; these seven focused cases do not establish full-stage runtime
acceptance. No production code or memory configuration changed.

Preparation verification against diagnostic-fix baseline `d95fa34` confirmed
exactly seven changed KQL hashes and **153 unchanged hashes**; the complete
literal data prefixes and observed/expected comparison tails of the seven cases
were byte-identical. The regenerated 160-entry manifest matched every fixture.
Configured pytest passed **585 tests with 96.15% coverage**, above the 80% gate;
Ruff and diff checks passed. No engine calls were made during preparation.
### Typed grouped retained-context join correction (2026-11-15)

The standard-mappings diagnostic on candidate `d95fa34` exposed a retained-context
regression: ValueSum was correctly 40, while User was empty rather than Alice and
Bob. The normalized union shared summary and source schemas. Its summary side
retained null source timestamp/context columns, so the subsequent join suffixed
the real source columns and the window predicate read the null summary timestamp.
The correction projects only the summary ID, window end, normalized grouping
identities, and partition before joining source matches.

A new public-conversion fixture, `typed_grouped_context.kql`, first reproduced
this independently: numeric Host 1/east had EventCount 2 and string Host "1"/east
and numeric Host 1/west each had EventCount 1, but every User/Server collection was
empty. Its literal expected collections were unchanged for the green run. The
parent variant checks the four observed User/Server relationships and forbids
cross-pairs through an exact result multiset comparison.

The first generic diagnostic attempt exposed a dynamic/string Region union
mismatch; its observed Region projection was corrected before the genuine red
run. The existing `context_nested.kql` diagnostic likewise required
`Host=tostring(Host)` to align with its expected table. Its literal expected data
and nested context type/relationship checks remain unchanged. These semantic
HTTP 400 responses are retained alongside the red/green evidence.

All requests were serial, owned by the retained-context repair workstream, on
`NetDefaultDB`, engine build `1.0.9815.14081`, product version
`2026.11.15.0741-2645-8e3387b-master`. The emulator and per-query cap remained
12 GiB (`12884901888` bytes), within the user's absolute 16 GiB total ceiling.
The client timeout was 360 seconds. No memory configuration changed and no
resource-failure retry occurred. Each passing response was also checked for
exactly one empty PrimaryResult and exactly one completion with both
`HasErrors=false` and `Cancelled=false`.

[Structured evidence](runtime-evidence/typed-retained-context-fix.json) contains
the records, fixture/request metadata, response hashes, primary diagnostics,
completion frames, and semantic-error payloads. Raw requests and responses remain
under the `/tmp/kusto-context-*` directories identified there. Full configured
pytest passed **586 tests**, with **96.16% coverage**; Ruff and diff checks passed.
This focused repair does not establish acceptance for unrelated combined-stage
fixtures or close the outstanding range-parser and partial-ordered gaps.

The focused engine outcomes were:

| Fixture | Seconds | Result |
| --- | ---: | --- |
| `standard_mappings.kql` | 21.111 | PASS |
| `context_event_count.kql` | 36.368 | PASS |
| `context_nested.kql` (Host diagnostic aligned) | 46.332 | PASS |
| `context_value_sum.kql` | 32.208 | PASS |
| `typed_grouped_context.kql` | 44.888 | PASS |

`typed_grouped_context_parent.kql` failed with curl error 52, "Empty reply from
server", before any response could establish a primary table or completion.
Engine requests stopped immediately; no resource-failure retry was attempted.
`standard_mappings_parent.kql` was not run in this workstream. Thus direct grouped
context is runtime-verified, while the new parent relationship fixture remains
runtime-incomplete. The transport failure alone does not establish OOM, and
read-only Docker inspection was unavailable due to socket permissions.

The branch incorporated integration `54a9a0d` (the separate average-fixture
Machine diagnostic alignment) before its final configured test run: 586 passed
in 21.71 seconds, 96.16% coverage. That merge changes the complete manifest hash;
the executed fixture hashes remain recorded separately in each runtime record.

Root subsequently inspected the container read-only: it exited with code 137,
and its logs explicitly reported `Crash_UnhandledException` with
`System.OutOfMemoryException` in `TimerQueue`, followed by the Kusto process
being `Killed`. Docker's `OOMKilled` flag was false; the runtime OOM diagnosis
relies on the explicit exception log. Both memory and memory-swap limits remained
`12884901888` bytes. This confirms the typed-parent attempt failed due to runtime
memory exhaustion. It remains incomplete and was not retried.

The focused seven-case preparation was subsequently merged with production
context correction `a75b98b`. Its typed-left projection repair and the separate
average Machine/context-nested diagnostic fixes were retained unchanged. The
seven cases regenerated with identical literal inputs and expected-result tails;
all **155 other fixture hashes** in the 162-entry candidate matched integration.
The all-fixture generation check and matrix/manifest consistency check passed
(two targeted tests); Ruff and diff checks passed. This merge performed no
engine calls, resource changes, or new runtime-completion claims.

## Integrated seven-by-seven matrix: all 49 PASS

The strict runner executed all 49 `matrix_*.kql` producer/consumer fixtures serially on the recorded Kusto emulator, database `NetDefaultDB`. Every query returned HTTP 200, exactly one empty primary diagnostic table and a successful, uncancelled completion. Literal expectations require one child contribution, metric 1 for count/temporal consumers and 2.5 for numeric consumers, with the parent window 11:55–12:00.

Execution started from `a75b98b`; all 49 fixture SHA-256 values were verified unchanged at integrated `876919c`. [Structured results](runtime-evidence/matrix-a75b98b.json) include each hash, duration, outcome, engine version and request options. Raw local requests/responses remain under `/tmp/kusto-final-matrix-a75b98b`. The emulator stayed at 12 GiB; no allocation change or retry occurred.

This establishes the small ungrouped producer/consumer matrix. Grouped context, mixed/multilevel cases and the remaining range dependency have separate gates; this result does not mark #32 or #17 complete.

## Merge checkpoint after null-safe retained collections (2026-11-16)

Integration commit `edd672a` excludes null/missing values from displayed retained-context
collections and from their collection-limit counts. The relationship rows remain intact,
so other observed fields and child membership are not discarded. This repairs an engine
failure in the empty numeric-context fixtures: Kusto flattened `pack_array(null)` and
omitted the resulting null from `make_list`, while the old guard counted it and raised a
false collection-limit assertion. All three empty numeric fixtures now return HTTP 200
with empty multiset diagnostics.

The regenerated 162-fixture manifest has SHA-256
`49e197f0ad2234cf460578d9d71ea3b11f13289c9f7f4486ba220c91b608da52`.
On the same engine `2026.11.15.0741-2645-8e3387b-master`, database
`NetDefaultDB`, and unchanged 12 GiB container and per-query cap:

- All 49 regenerated `matrix_*.kql` producer/consumer fixtures passed strict
  serial execution with HTTP 200, one empty primary diagnostic, and a successful,
  uncancelled completion.
- `standard_mappings.kql` and `standard_mappings_parent.kql` passed at hashes
  `7b0017b6...` and `5485fc26...`; the parent completed in 180.369 seconds.
- `typed_grouped_context.kql` and `typed_grouped_context_parent.kql` passed at
  hashes `89a19e9b...` and `63300096...`. The parent completed in 336.777 seconds
  with request option `servertimeout=10m`; the memory cap was unchanged.
- The seven simplified numeric fixtures passed: sum, average, percentile, their
  empty-population variants, and the adjacent-integer boundary case.
- The seven typed-grouping fixtures changed by the tuple-restoration patch passed
  at their current hashes. The two unchanged typed chain fixtures retain their
  earlier passes. Both dependency-order modes and `value_avg_mapped.kql` are also
  byte-identical to their passing hashes recorded immediately before the null fix.
- The configured Python suite passed 589 tests with 96.16% coverage. Repository-
  pinned Ruff and `git diff --check` passed.

Acceptance remains intentionally incomplete. `numeric_context_metrics.kql`, SHA-256
`30b6947576c21b4cf53868dbb1f8c188c987275e86473041d84eecabe1a0e95b`,
failed after 137.437 seconds with HTTP 520. Emulator query history records
`System.OutOfMemoryException`; Docker remained running and both limits remained
`12884901888` bytes. The remaining grouped/mixed/multilevel numeric and temporal
fixtures were not completed in this stream. Issue #31 still depends on released
upstream range parsing, and partial ordered thresholds remain deferred. These gaps
are follow-up work; they are not converted into acceptance passes by merging the
available implementation.

## Table-sensitive source predicates (2026-11-16)

`source_predicates_securityevent.kql` passed the strict v2 runner on Kusto emulator
`2026.11.15.0741-2645-8e3387b-master`, database `NetDefaultDB`, with the unchanged
12 GiB per-query memory cap. HTTP 200, one empty primary multiset diagnostic, and
a successful uncancelled completion establish the two independently expected
four-distinct-group Alice windows. The source retains EventID 4799,
CallerProcessId 0x0, and all four privileged-group predicates after custom
SecurityEvent selection and Microsoft XDR processing. Standard field mappings,
a per-source counted-field alias, and explicit detection-value normalization are
included. This small predicate regression does not establish throughput.

[Results and fixture hash](runtime-evidence/source-predicates/results.json),
[engine response](runtime-evidence/source-predicates/engine-version.response.json),
and [query response](runtime-evidence/source-predicates/source_predicates_securityevent.response.json)
retain the execution evidence. The [executed query](runtime-evidence/source-predicates/executed-securityevent.kql)
and [executed manifest](runtime-evidence/source-predicates/executed-manifest.json)
preserve the exact passing inputs at fixture SHA-256
`4645446c9123326b63007bb42538658630f472f9b8b39e8c8a0c151d12a4a120`.

After merging the scalar-schema implementation, the current fixture uses native
scalar output and checks its complete public schema (`WindowStart:datetime`,
`WindowEnd:datetime`, `Actor:string`, `ValueCount:long`) as well as the result
multiset. Its SHA-256 is `120494d80e695613471489b7effb949b069b43c6bd69ac003034d809ce010c10`.
This changed query is **pending integrated engine execution**; the prior pass is
predicate evidence for the archived query. Re-run the current targeted
`source-predicates-manifest.json` at the integrated acceptance gate.
