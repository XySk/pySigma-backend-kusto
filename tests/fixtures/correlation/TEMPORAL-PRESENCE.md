# Unordered and Boolean temporal presence (#40)

Unordered detection batches source memberships once per native group and event timestamp, then updates one `Last<Source>Time` datetime per distinct resolved source. A source is present when its last match is within the inclusive trailing timespan. The generated Boolean condition uses named `Has<Source>` expressions directly; numeric conditions compare their sum as `RuleCount`. Decimal comparison for fractional thresholds prevents native long-literal equality coercion from changing matches. Sorted scans reset state between native groups, including the special scalar equality handling needed for real NaN groups.

Only source-match timestamps establish anchors. A source used under negation still establishes anchors; expiration alone never does. Batched ties, repeated references, aliases, child `WindowEnd` inputs, overlapping windows, and required scalar `RuleCount` remain supported. The context contributor relation remains separate; its ordered/Boolean contributor optimization belongs to #47.

The public conversion red/green test is `tests/test_temporal_presence_kusto.py`. The initial 12 literal expected-result fixtures are frozen in `presence-manifest.json`; another five existing Boolean fixtures cover presence, alternatives, parser precedence, nested negation, and mixed chains. All 17 passed the strict v2 real-engine harness with empty multiset diagnostics and successful uncancelled completion frames. Raw core evidence: `/tmp/ticket40-runtime/core`; combined frozen input: `/tmp/ticket40-core`. Engine: `2026.11.15.0741-2645-8e3387b-master`.

## Measured comparison

The baseline is pinned before redesign at `07525ee846878754bda7933eab0684895a156086`. Candidate production revision is `bb07f55bc264c949c2d1577f6fb48e25b99b4b8e`. The two cases below reuse the exact baseline input/oracle fixture hashes. Both baseline and candidate passed all three repetitions, including exact result multisets and declared schema checks under the explicit dynamic-to-native scalar migration. The shared container/image/version and 12 GiB limits match; query caching is disabled, with 15-second client and 13-second server limits.

| 96-row workload | Query bytes before → after | Median engine seconds | Median total CPU seconds | Median peak bytes |
| --- | --- | --- | --- | --- |
| 12 groups × 8 events | 14,980 → 1,900 | 0.987 → 0.051 | 0.766 → 0.049 | 6,444,864 → 2,118,352 |
| One hot group, timestamp ties | 15,334 → 2,339 | 0.935 → 0.076 | 0.724 → 0.075 | 6,576,176 → 2,139,536 |

Detection now uses one aggregation, sort, and scan with no join or expansion. The former baseline used eight joins and five expansions, including native-scalar work subsequently removed by #37. Therefore these improvements describe the combined scalar redesign and temporal strategy, not an isolated attribution to the scan change. Most elapsed improvement is query planning; these small datatables do not establish production capacity or scaling. Intermediate per-operator growth is not observed. Plain cases do not measure retained-context work.

[Machine-readable samples and query shapes](runtime-evidence/temporal-presence-bb07f55.json) preserve each elapsed, CPU, planning/execution CPU, peak-memory sample, query/fixture hash, engine/image/resource settings, and raw evidence location. Frozen candidate files: `/tmp/ticket40-bench-candidate`; raw baseline: `/tmp/kusto-spec34-baseline-fast`; raw candidate: `/tmp/ticket40-runtime/benchmark-verified`. The first sandboxed benchmark attempt failed before executing queries because Docker inspection was unavailable; that failed record remains in `/tmp/ticket40-runtime/benchmark`.

After merging relational context integration `d1301d1`, another 16 literal context/chain cases passed at `/tmp/ticket40-runtime/context-verified`. One extra public red/green regression and engine case checks a scalar-grouped temporal parent consuming a context-bearing child; it passed at `/tmp/ticket40-runtime/scalar-child-context`. This closes an integration edge where narrow temporal normalization projected an unnecessary child ID. The extra fixture is included in the final presence manifest. A sandbox-only transport failure before executing the context gate remains at `/tmp/ticket40-runtime/context`.

Broad final corpus regeneration, all-type/chained performance, and production-scale evidence remain integrated acceptance work for #49. These results do not close #31/#32 or establish unmeasured context performance.
