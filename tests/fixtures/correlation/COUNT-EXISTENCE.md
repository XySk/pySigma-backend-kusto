# Consumer-aware child event counts

An internal `event_count` child with an integer `gte: N` or `gt: N-1` can omit its actual count when every consumer needs only matching timestamps and grouping fields. The collection-wide plan uses resolved Sigma references and generation controls, including shared and multilevel consumers. Standalone child output, callbacks, requested contributor context, or a consumer requiring a scalar child metric keeps the general count plan. Requirements include aliases and context inherited from other sources. Other comparisons and thresholds outside the verified predecessor-offset range (thresholds above 1,025) retain the general plan.

For threshold N, source-match rows remain individual rows through a native-group/time sort. A timestamp qualifies if its Nth-most-recent match belongs to the same group and lies within the inclusive child window. Duplicate source searches and duplicate observations therefore retain their weight. Qualifying group/timestamp pairs become distinct only after the predecessor test. Threshold one needs only the valid distinct group/timestamps, without sorting or predecessor state. Native real NaN group identities receive the same equality treatment as the general count plan.

The generated `FailedLogonBursts` example in `count_existence.py:targeted_queries()` searches SecurityEvent failures (`EventID == 4625`) and successes (`EventID == 4624`). Logical `User` maps to `TargetUserName`; the rule groups across computers. It requires ten failures in ten minutes, followed strictly later by a success within another ten minutes. Its parent state is named `LastFailedLogonBurstTime`. Ten failures from 00:00 through 00:09 qualify for success at 00:18 and at the inclusive 00:19 endpoint. A simultaneous first burst and success do not qualify; a simultaneous new burst does not hide an older eligible burst.

Run the focused preparation and correctness gate explicitly after obtaining the shared engine lane:

```sh
python -m tests.fixtures.correlation.count_existence
python tests/fixtures/correlation/runtime.py \
  --manifest tests/fixtures/correlation/count-existence-manifest.json \
  --output /tmp/count-existence-runtime --timeout 45
```

The focused fixtures compare the specialized and general public-conversion paths against independent literal or direct window-count expectations. They include seeded realistic streams, duplicate searches, many timestamp ties, multiple accounts and successes, unequal child/parent spans, native real group identities, and a sibling context alias requiring the actual child metric. An identity callback requests the general plan through the public API; no alternate parser or testing-only strategy option is used.

`freeze_performance(destination)` prepares four paired populations for the existing
strict benchmark harness: 38,400 rows across 64 groups, 30,000 hot-group rows,
6,400 sparse nonqualifying rows and 30,000 threshold-one rows. All 24 measured
samples passed exact result multisets, native schemas and successful completion.
The [full strategy report](runtime-evidence/count-existence-performance/README.md)
archives unchanged queries/oracles, raw responses, hashes and resource settings,
with client/engine elapsed, total/planning/execution CPU and peak-memory samples.
All four elapsed medians decrease; sparse total CPU and threshold-one memory
increase. These synthetic measurements support this strategy on the measured
populations without a production capacity or universal resource-improvement claim.

Optional report bounds now extend the same resolved graph plan with maximum
accumulated source lookback. Public report cutoffs never truncate the child's
complete history or internal relational context before a parent consumes it.

## Verified checkpoint

All six frozen correctness fixtures passed on Kusto `2026.11.15.0741-2645-8e3387b-master` with a 12 GiB per-query memory limit. Each completed with an empty multiset diagnostic. Results, frozen hashes, engine version, and raw response frames are preserved under `runtime-evidence/count-existence/`. A first diagnostic represented signed zero as `0.0` while both correct native plans returned `-0.0`; that failed run is retained. The corrected diagnostic canonicalizes zero and tests positive and negative zero within the same native group.

The original targeted example generates 2,192 bytes across 33 lines, compared with 2,587 bytes across 38 lines for the general plan. It omits the child count and expiration stages. These structural measurements complement the separately recorded paired engine measurements; query size alone does not establish performance. Their original measured hashes predate final integrated naming cleanup.
