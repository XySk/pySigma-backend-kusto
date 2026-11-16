# Consumer-aware child event counts

An internal `event_count` child with an integer `gte: N` or `gt: N-1` can omit its actual count when every consumer needs only matching timestamps and grouping fields. The collection-wide plan uses resolved Sigma references and generation controls, including shared and multilevel consumers. Standalone child output, callbacks, requested contributor context, or a consumer requiring a scalar child metric keeps the general count plan. Requirements include aliases and context inherited from other sources. Other comparisons and thresholds outside the positive Kusto predecessor-offset range retain the general plan.

For threshold N, source-match rows remain individual rows through a native-group/time sort. A timestamp qualifies if its Nth-most-recent match belongs to the same group and lies within the inclusive child window. Duplicate source searches and duplicate observations therefore retain their weight. Qualifying group/timestamp pairs become distinct only after the predecessor test. Threshold one needs only the valid distinct group/timestamps, without sorting or predecessor state. Native real NaN group identities receive the same equality treatment as the general count plan.

The generated `FailedLogonBursts` example in `count_existence.py:targeted_queries()` searches SecurityEvent failures (`EventID == 4625`) and successes (`EventID == 4624`). Logical `User` maps to `TargetUserName`; the rule groups across computers. It requires ten failures in ten minutes, followed strictly later by a success within another ten minutes. Its parent state is named `LastFailedLogonBurstTime`. Ten failures from 00:00 through 00:09 qualify for success at 00:18 and at the inclusive 00:19 endpoint. A simultaneous first burst and success do not qualify; a simultaneous new burst does not hide an older eligible burst.

Run the focused preparation and correctness gate explicitly after obtaining the shared engine lane:

```sh
PYTHONPATH=. python tests/fixtures/correlation/count_existence.py
PYTHONPATH=. python tests/fixtures/correlation/runtime.py \
  --manifest tests/fixtures/correlation/count-existence-manifest.json \
  --output /tmp/count-existence-runtime --timeout 45
```

The six frozen fixtures compare the specialized and general public-conversion paths against independent literal or direct window-count expectations. They include seeded realistic streams, duplicate searches, many timestamp ties, multiple accounts and successes, unequal child/parent spans, native real group identities, and a sibling context alias requiring the actual child metric. An identity callback requests the general plan through the public API; no alternate parser or testing-only strategy option is used.

`freeze_performance(destination)` prepares four paired workloads for the existing `benchmark.py run` harness: 38,400 rows across 64 groups, 30,000 rows in one hot group, sparse nonqualifying activity, and threshold one. Both plans must return the independently counted result multiset with the declared schema. The runner preserves frozen hashes, engine version, resource settings, repeated elapsed samples, CPU, and peak memory when exposed. Measured results are recorded below after the exclusive engine run. Query bytes or passing Python tests alone establish no performance claim.

No report interval or implicit source-time restriction is introduced by this change. Future report planning can extend `_prepare_correlation_graph` over the same resolved collection. It must preserve complete accumulated source lookback and the relational context artifacts independently of public output bounds.
