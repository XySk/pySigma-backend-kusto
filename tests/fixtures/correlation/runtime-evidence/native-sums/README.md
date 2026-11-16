# Native sum evidence (#42)

The strict runtime gate passed 26 frozen public-conversion fixtures, then one
replacement supported-range integer fixture. Production sum implementation is
`5aeadf0`; benchmark candidate includes the clean merge `1b680826` of integration
`a134c76`. The old benchmark is frozen from `e9e7775`, after scalar schema support
and before the sum strategy replacement. These are matched sum-algorithm
measurements, separate from the original pre-redesign #35 baseline.

Integer inputs use native long weighted arrival/expiration boundaries, with an
explicit arrival marker preserving zero, negative, and unusable-input anchors.
An arrival at an expiration timestamp evaluates first, preserving inclusive
windows. Real inputs use finite-only native sums over actual current-window
pairs; large expired values never enter a later running total. Decimal inputs
retain native decimal precision. Native int sums expose long child metrics.
Decimal casts used for fractional integer comparisons do not widen arithmetic.
All native input and intermediate arithmetic must remain in supported ranges.

Correctness includes literal independently expected source multiplicity, mixed
aliases and child metrics, retained-value/group relationships and deduplication,
empty child contexts, all fractional comparisons, values above double integer
precision, decimal precision, int-to-long promotion, native real NaN/infinity/
signed-zero groups, and a large real value expiring before small observations.
The supported-range replacement removes the old maximum-plus-one cancellation
fixture's dependence on overflowing intermediates.

`correctness-fixtures/manifest.json` pins the executed 26 queries; `correctness/`
contains raw requests, responses, execution manifest and engine version. The
supported-range replacement has separate fixture/evidence directories.
`prior-empty-fixture-failure/` preserves the failed first run: legacy fixture
normalization converted nonfinite reals to long, yielding the signed minimum.
The correction declares and normalizes that intended nonfinite population as
real, preserving the independent expected zero. Empty average and percentile
variants use the same accurate real declaration, preserving undefined-population
expectations. Historical custom-overflow proof remains in RUNTIME.md and its
original evidence; those failures are outside the current supported-input
contract.

Both benchmark sets passed all 24 samples (8 workloads, 3 repeats each), including
native output schemas and exact expected multisets. Workloads have 96 source
rows: 12 groups of 8 qualifying values or one tied hot group with no qualifying
windows, each with context off/on and long/real measurements. Real values are
exact quarter increments. Runs used the same engine
`2026.11.15.0741-2645-8e3387b-master`; query cache age was zero and per-query memory
was 12,884,901,888 bytes. `preceding-container-resources.json` records the container
snapshot from the immediately preceding exclusive lane, with the same memory and
swap limits. The sum runner did not recapture a separate container inspection.

All entries below are medians, before → after. Wall time includes local request
overhead; engine elapsed, CPU and peak memory come from the engine's resource
statistics. Raw responses retain every sample. Integer plain queries remove the
candidate-pair join; real plain queries retain one necessary current-window join.
No per-operator intermediate row counts were captured.

| Workload | Query bytes | Wall seconds | Engine seconds | CPU seconds | Peak MB |
| --- | ---: | ---: | ---: | ---: | ---: |
| `long-many-groups-value_sum-plain` | 3540 → 957 | 0.026938 → 0.015054 | 0.019089 → 0.006620 | 0.004143 → 0.003239 | 4.61 → 1.58 |
| `long-many-groups-value_sum-context` | 18789 → 2530 | 0.407095 → 0.021454 | 0.399365 → 0.011870 | 0.169660 → 0.009430 | 5.88 → 7.95 |
| `long-hot-group-value_sum-plain` | 3548 → 961 | 0.028574 → 0.011962 | 0.021210 → 0.005103 | 0.004181 → 0.004959 | 4.10 → 1.58 |
| `long-hot-group-value_sum-context` | 18805 → 2534 | 0.378063 → 0.017841 | 0.370831 → 0.009987 | 0.137596 → 0.007394 | 8.08 → 1.58 |
| `real-many-groups-value_sum-plain` | 3558 → 821 | 0.027531 → 0.013509 | 0.019793 → 0.006368 | 0.010681 → 0.005711 | 4.50 → 4.51 |
| `real-many-groups-value_sum-context` | 18825 → 2394 | 0.398017 → 0.023351 | 0.386940 → 0.015476 | 0.152814 → 0.010743 | 6.27 → 8.02 |
| `real-hot-group-value_sum-plain` | 3566 → 825 | 0.027630 → 0.014174 | 0.020302 → 0.006555 | 0.009801 → 0.005993 | 4.22 → 4.05 |
| `real-hot-group-value_sum-context` | 18841 → 2398 | 0.382830 → 0.019264 | 0.374572 → 0.012347 | 0.137054 → 0.011257 | 8.08 → 4.34 |

Every measured workload reduced elapsed time and CPU. Many-group context cases
used more peak memory (long about 5.88 → 7.95 MB; real about 6.27 → 8.02 MB), while
other cases stayed similar or decreased. The populations are small, timings
include substantial compilation/planning overhead, and the emulator was local.
These results demonstrate these query shapes; they do not establish production
capacity, large-window scaling, or every possible sum workload. Final integrated
regeneration and acceptance remain #49's responsibility.
