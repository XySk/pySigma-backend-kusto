# Exact scalar value counts (#39)

Each usable scalar observation is active from its event time through its event time plus the timespan. Sorting native group/value/time rows identifies the starts and ends of merged overlapping intervals without assigning value identities or expanding event/window pairs. Each requested field has its own interval stream and count. A final sorted accumulation processes every start before original source anchors and every equal-time expiration afterward. Missing counted values remain in the source anchor relation. Expiration alone never evaluates the rule.

Native scalar values remain native, including long/decimal precision, datetime, timespan, bool and guid values. Real NaNs follow native summarize equality for both counted values and grouping fields. Fractional thresholds use an explicit decimal comparison against the long result; the metric itself remains long. Source projections carry only timestamps, groups and requested counted values. Complete contributor context continues to use the separate relational artifact introduced by #46.

The focused public-conversion fixtures cover fractional comparisons, real NaN/infinity/zero values and groups, scalar types, and a parent counting a retained User that also fixes its group. The last case has two child summaries for Alice/server1 but only one distinct User. The initial engine selection mistakenly reused that helper's event-count expected value of two. That failed input/response remains at `/tmp/ticket39-runtime/intervals` and `/tmp/ticket39-runtime/evidence-intervals`. The helper now independently expects one distinct User, and the explicit corrected fixture is retained as `value_count_grouped_counted_context.kql` for the integrated engine gate.

Seventeen other frozen cases passed strict real-engine multiset checks with successful uncancelled completions: aliases, boundaries, all comparisons, independent fields, empty-value anchors, precision, direct/chained sources, complete context, and privileged-group enumeration counts/schema. Frozen input is `/tmp/ticket39-runtime/intervals-v2`; raw evidence is `/tmp/ticket39-runtime/evidence-intervals-v2`. The privileged fixture preserves EventID 4799, CallerProcessId and all four group predicates.

## Strategy comparison

The alternative is the native scalar exact candidate join at integration revision `d1301d1`, after recursive identity removal. Both alternatives use the same 500 source matches at one-second intervals in one group, a five-minute window, all 500 anchors, and identical literal expected results. Repeated values cycle through four users; the other workload has 500 unique users. Each strategy/profile passed three sequential samples on engine `2026.11.15.0741-2645-8e3387b-master` with a 12 GiB per-node query memory limit and 45-second client timeout.

| Profile | Query bytes, candidate join → intervals | Median engine seconds | Median total CPU seconds | Median peak bytes |
| --- | --- | --- | --- | --- |
| Four repeated users | 1,017 → 1,691 | 0.037926 → 0.013484 | 0.029264 → 0.009947 | 54,235,056 → 3,147,200 |
| Unique users | 1,017 → 1,691 | 0.032230 → 0.016497 | 0.026871 → 0.014742 | 55,218,896 → 3,147,200 |

These measurements select merged intervals for both populations: CPU, elapsed time and peak memory improve while both exact result multisets agree. The longer text describes interval boundaries explicitly; it removes candidate joins and expansions. These small datatables are strategy evidence, not production capacity measurements. Per-operator intermediate growth was not observed. Broad profiles, context performance and integrated chains remain #49 acceptance work.

[Machine-readable samples](runtime-evidence/value-count-strategies.json) retain all measurements, fixture/query/response hashes, engine and resource metadata, and raw evidence locations. Baseline frozen inputs are `/tmp/ticket39-runtime/baseline`; repeated samples are `/tmp/ticket39-runtime/comparison-{1,2,3}-{baseline,intervals-v2}`.
