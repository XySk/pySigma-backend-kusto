# Native scalar correlation fixtures

These self-contained KQL comparisons are generated through ordinary Sigma YAML collection parsing, real processing pipelines/options, and `KustoBackend.convert()`. Expected result multisets are independently worked literal examples or direct window enumeration. Every valid comparison returns exactly one empty primary diagnostic table; an extra, missing, or duplicated summary produces a difference row. Run the entire file, including its expected-result comparison.

The current contract uses declared native scalar timestamp, grouping, counted/measured, and retained fields. Built-in schemas or custom partial `BaseSchema` declarations resolve relevant types after transformations. Dynamic arrays, bags, and mixed types require explicit extraction or normalization in the pipeline. `scalar_inputs.py` visibly normalizes legacy malformed-input fixture populations; the backend never guesses their types. Files retaining historical names such as `context_nested` and `value_count_typed` now exercise normalized native scalars. Their former arbitrary nested-identity behavior is superseded.

Numeric inputs and intermediate arithmetic must stay within sensible native ranges. Integer sums are not widened merely to expand range; custom overflow assertions are retired. All supported current fixtures require successful execution. Historical overflow failures remain in [RUNTIME.md](RUNTIME.md), outside the current contract.

The reference-aware relation-binding revision passed all 255 fixtures, 384
repeated broad samples, and 42 focused samples. [Current hash-scoped
evidence](runtime-evidence/relation-binding-final/README.md) preserves the final
and rejected prototype queries, raw results, and paired cache decisions. The
[source-shaping evidence](runtime-evidence/source-shaping-final/README.md),
[integrated acceptance](runtime-evidence/integrated-acceptance-final/README.md),
and [per-workload comparisons](BENCHMARK-COMPARISON.md) remain comparison
baselines.

The reference-aware cache and contract-specific deduplication decisions are
listed in the [relation-binding audit](RELATION-BINDING.md), including their
consumer counts, safe reductions, sort locality, and focused measurement cases.
The [context-transport scale decision](CONTEXT-TRANSPORT.md) records the rejected
separate-summary/evidence prototype and the retained union contract. Its
[hash-scoped evidence](runtime-evidence/context-transport-final/README.md)
contains 150 paired scale samples and both initial failed prototype queries.

## Coverage and independent oracles

| Fixture family / generator | Behavior covered |
| --- | --- |
| `event_count_sliding.py`, count fixtures in `generate.py` | All comparisons, fractional long comparisons, duplicates/source-search multiplicity, ties, grouped/ungrouped windows, inclusive endpoints and one tick outside, invalid timestamps/groups, original anchors and no expiry-only evaluation |
| `value_count_sliding.py`, `value_count_*` | Independent counted fields, active intervals, null/empty anchors, native real NaN/infinity/signed zero and extended scalar types/precision; grouped counted-context values |
| `temporal_presence.py`, `boolean_generate.py` | Distinct immediate source presence, Boolean alternatives/precedence/negation, aliases and missing sources |
| `ordered_state.py`, ordered fixtures in `generate.py` | Earlier viable prefixes, strict ties and full-span endpoints, repeated references, grouped/ungrouped order, completion-only output, native real groups |
| `native_sum.py`, `native_average.py`, `native_percentile.py`, `average.py` | Native schemas and precision, finite-only measurements, zeros/negatives/multiplicity, null anchors, large expired observations, fractional comparisons, undefined avg/percentile including `neq`, exact nearest rank and extrema |
| `context.py`, `count_context.py`, `numeric_context.py` | Distinct scalar displays and observed cross-field relationships, once-per-child/group contributions, direct multiplicity, logical aliases and required scalar child metrics |
| `relational_context.py` | Displays at 999/1000/1001, configurable limit, `ContextComplete`, complete hidden 1001-pair parent inputs, callback-modified metric eligibility, no invented cross-pairs |
| `context_transport_benchmark.py` | Increasing density/size, hot/many groups, repetition, selectivity, context fan-out, child reuse/depth, numeric pairing, and modeled transport row/byte growth |
| `ordered_context.py`, `temporal_participants.py`, `boolean_context.py` | Every successful ordered and satisfied positive Boolean alternative; unrelated/absent evidence excluded; structural child presence without fabricated retained values |
| `empty_child_context.py` | Absence-only children anchor zero sum/count without values; average/percentile stay undefined under `eq` and `neq`; scalar child metric grouping and exclusion of absent retained groups |
| `temporal_context.py`, `dependency_order.py`, chained fixtures | Mixed/direct/shared/multilevel sources, name/UUID references, aliases, generation controls, reversed document order, accumulated spans and child `WindowEnd` timestamps |
| `reporting.py` | Optional half-open report bounds, 100 ns boundary ownership, inclusive lookback, maximum shared accumulated history, standalone child cutoff, complete prereport context, append-only callbacks and collisions |
| `standard_mappings.py`, `typed_grouping.py`, `typed_grouped_context.py` | Source-specific field/table mappings, logical scalar types and context, escaped columns and compatible child/direct aliases |
| `source_predicates.py` | SecurityEvent EventID 4799, CallerProcessId 0x0 and all four privileged-group names survive XDR processing; exact enumeration output and schema |
| `count_existence.py` | General versus Nth-match specialization; duplicate searches, randomized streams/ties, actual metric requirements, target-account failed-logons-then-success example |
| `integrated.py` | All 49 producer/consumer edges as singleton retained-value smoke comparisons |

The 49-edge matrix uses one child match and projects its metric as real for a common comparison. It proves edge availability and result values for that case. Rich stage fixtures separately establish multiplicity, native public schemas, shared children, overlapping windows, multi-step order, mappings, and generation controls; the matrix alone does not establish those properties for every pair.

The authentication example maps logical User to `SecurityEvent.TargetUserName`, preserving EventID 4625/4624 and grouping across computers. Ten failures in ten minutes followed strictly later by success within another ten minutes use child `WindowEnd` semantics. See [COUNT-EXISTENCE.md](COUNT-EXISTENCE.md). The enumeration example retains wrong-ID/process/group negative controls and two overlapping qualifying windows across computers.

## Freeze and execute

From the repository root, regenerate the dependency fixtures first, then the main corpus and every existing scoped manifest:

```sh
python -m tests.fixtures.correlation.dependency_order
python -m tests.fixtures.correlation.generate
```

`acceptance-manifest.json` freezes every top-level `.kql` file, including separately prepared fixtures. Scoped manifests retain explicit memberships and refresh their hashes from that same corpus. The manifest's `PENDING` is a preparation marker, not an execution verdict; actual results are hash-scoped in the execution ledger. Pytest checks deterministic public conversion and diagnostics but does not execute KQL.

Reserve the shared engine lane and execute the frozen corpus:

```sh
python tests/fixtures/correlation/runtime.py \
  --endpoint http://localhost:8080 --database NetDefaultDB \
  --manifest tests/fixtures/correlation/acceptance-manifest.json \
  --output /tmp/kusto-integrated-evidence --timeout 60
```

Use `--only <fixture.kql> ...` for a selected subset. The runner captures engine version, query/manifest hashes, request options, raw requests/responses, durations and strict results. Each pass requires one empty PrimaryResult, exactly one successful uncancelled completion, and no engine error frames. Partial output, differences, missing/multiple/cancelled completion frames, HTTP/transport errors, timeout, and memory failure all fail. A missing reply is never an empty successful result. Hash drift fails before execution. The runner stops on the first failure and atomically preserves each completed result; an interrupted partial run remains RUNNING rather than passing unexecuted cases. Keep failed runs before fixing and replaying.

Displayed collections default to at most 1,000 distinct values per field and report `ContextComplete=false` when incomplete. Parents use complete uncapped relationship rows, preserving observed tuples; they never consume display arrays. Rules requesting no context omit collection/completeness work. Engine limits still apply to complete internal relations and materialization.

[Runtime evidence](RUNTIME.md) separates current hash-scoped results from historical contracts and failures. [BENCHMARK-BASELINE.md](BENCHMARK-BASELINE.md) freezes the original pre-redesign 64 workloads and all failures; separate strategy reports retain their own revisions, deadlines and populations. Synthetic emulator measurements do not establish production throughput/capacity. No numerical SLA is claimed.

The upstream range-parser dependency remains tracked in #31. #32's outstanding acceptance and partial ordered-threshold semantics remain separate; this corpus introduces neither a local parser nor new partial ordered behavior and does not close those issues.
