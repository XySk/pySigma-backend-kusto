# Correlation relation-binding audit (#52)

Correlation query bindings use one explicit policy. A binding records its
expression, consumer count, completed safe reductions, and whether repeated
evaluation must preserve a generated identity or other nondeterministic value.
Ordinary `let` is the default. `materialize()` is selected only for genuine
reuse, stable evaluation, or a separately measured exception. This revision has
no measured-only exception.

## Decisions

| Relation | Consumers and reductions | Decision |
| --- | --- | --- |
| `CorrelationMatches` | Public summaries and/or complete context artifacts; the expression assigns `CorrelationMatchId` with `row_number()` after the summary has reached its public schema. | Materialize to keep one stable ID assignment across summary and evidence rows. |
| `WindowSourceMatches` | One exact-window context projection for ordinary count/numeric/unordered context; two consumers for Boolean presence/evidence; multiple candidate consumers for long ordered sequences. | Ordinary for one consumer. Materialize only for Boolean reuse or long ordered reuse/stable relationship IDs. |
| `BooleanParticipants` | One relationship consumer after positive-alternative membership has been applied. | Ordinary. The reused input is `WindowSourceMatches`, not this reduced output. |
| Ordered participant filters | One final relationship consumer after endpoint filtering or prefix/suffix reachability. | Ordinary. Reused prefix/suffix witness relations remain cached only where their declared consumer count exceeds one. |
| `CompleteContextRelationships` | Exact `(CorrelationMatchId, requested values...)` tuples after projection and `distinct`. One consumer for one displayed field and no parent; otherwise one per displayed field plus the complete parent artifact. | Ordinary for one display-only consumer; materialize for multiple display fields or a parent consumer. No provenance-bearing tuple is removed. |
| Shared `*Results` child bindings | A child public summary/evidence relation can be selected repeatedly, and its correlation IDs must remain stable across those selections and parents. | Materialize for stable evaluation. |
| `ParentSourceMatches` | Direct and child branches consume the same canonical relation after optional child identity normalization. Direct multiplicity must remain intact; child-only deduplication occurs after the split at `(child, resulting group[, retained numeric value])`. | Materialize before the split. No generic source-row deduplication is safe here. |
| `PercentileWindowValues` | Exact window candidates are reused for population counting and rank selection after unusable measurements, out-of-window rows, and unused columns are removed. | Materialize the narrow reused candidate relation. Endpoints 0/100 continue to use ordinary extrema without this binding. |
| `ValueCountSources` | Original anchors plus one interval builder per counted field consume the canonical source relation. Empty counted values remain in the anchor consumer. | Materialize for reuse; filtering counted values cannot be pushed into this shared binding. |
| `Counted*Activity` | Interval starts and ends consume rows only after empty-value filtering, deduplication at group/value/timestamp identity, local sort, and merged-interval boundary marking. | Materialize the reduced relation for its two consumers. |

Event-count group/timestamp weights, value-count group/value/timestamp activity,
and exact context relationship tuples are the only contract-specific reductions.
Raw source-match multiplicity and distinct overlapping correlation matches are
never generically deduplicated.

Each order-sensitive sort remains immediately before its `row_cumsum`, `scan`,
neighbor comparison, or percentile-rank consumer. The sort input is projected,
filtered, or aggregated first where the contract permits it.

## Measurement coverage

The unchanged 64-workload benchmark remains the broad gate: sparse, dense,
many-group, and hot-group inputs cover increasing density; hot value-count data
repeats values while dense/many-group data is mostly unique; plain/context pairs
cover absent/present evidence consumers; ordered context and mixed multilevel
chains cover sequence evidence and child reuse.

`relation_binding_benchmark.py` adds seven focused public-conversion workloads:
single-display sparse/dense/hot inputs, repeated versus unique values, two
display consumers, Boolean context, ordered context, and a context-bearing
multilevel child. Every run uses the same exact independent result comparison,
CPU/peak-memory capture, disabled result cache, resource limits, and failure
rules as the established benchmark harness.

Adoption requires at least five successful samples. A targeted performance
candidate must lower median CPU or peak memory by at least 15% and must not
confirm a regression above 10% in representative unaffected workloads. If a
candidate misses that threshold, its query and samples are retained as negative
evidence and the corresponding existing cache decision remains unchanged.
