# Exact native percentiles (#44)

Production percentile revision `dba2669`, integrated with average/reporting revision `394d9ee` at `d2cc22f`. The old percentile strategy is frozen from `40bc40c`, after scalar/count/temporal/context/native-sum foundations. This is a paired percentile strategy comparison, separate from the original pre-redesign baseline under #35.

Correctness: **15/15 initial cases PASS**, followed by **17/17 integrated final cases PASS** against Kusto `2026.11.15.0741-2645-8e3387b-master`, `NetDefaultDB`, localhost:8080. Both frozen fixture sets and raw requests/responses, engine version, hashes and strict completion verdicts are archived separately. Missing completion, cancellation, query errors or nonempty multiset diagnostics fail the gate. The initial prefilter uses decimal threshold comparisons; final fixtures use native comparisons for integral thresholds and decimal casts only for fractional long comparisons.

Literal independent expected multisets cover the median of [1,2,100,200] = 2; selectors 0,1,50,99,100; all six comparisons; fractional thresholds and positive equality with tied zeros; native int/long/real/decimal observations; adjacent large integers and decimal precision; finite zero and duplicates; NaN/infinity exclusion; missing-value anchors; inclusive endpoints and one tick outside; undefined populations matching no operator; source aliases; scalar metric chains; mixed retained child/direct sources; child/group/value dedup with direct multiplicity; and native real NaN/infinity/signed-zero groups. Required ValuePercentile output remains present.

Query shape: project declared native measurements early, construct exact-time candidate windows, then preserve a narrow window/group/value relation once for population counting and selection. One native sort and row_number select the exact nearest rank; no dynamic numeric sorting keys, approximate percentiles, capped arrays or truncation are used. Native min/max at selectors 0/100 eliminate counting, materialization and sorting. Every original valid source row remains an anchor even when its measurement is unusable. Sorting partitions use native groups with an explicit NaN-aware reset. The candidate join can still grow quadratically in a hot window.

Paired old/new benchmark: **48/48 samples PASS** across long/real × many groups/one hot group × context off/on × three repetitions × old/new. Each workload has 96 inputs and the same independent direct-window expected output and required native metric schema. Context cases additionally check exact collections and ContextComplete.

Per-query memory limit is 12,884,901,888 bytes, timeout 45 seconds and benchmark cache age zero. The existing kusto-spec-17 container, image and memory/swap limits come from the immediately preceding root baseline lane snapshot in preceding-container-resources.json. Engine statistics are captured from each actual response. Each table cell is old → new, medians of three successful samples; CPU is engine-reported total seconds and peak memory is MB (bytes/1,000,000).

| Workload | Wall seconds | Engine seconds | CPU seconds | Peak MB | Bytes | Lines |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| long-many-groups-value_percentile-plain | 0.032322 → 0.018347 | 0.021482 → 0.010191 | 0.018146 → 0.006127 | 4.467792 → 4.362000 | 1853 → 1591 | 28 → 28 |
| long-many-groups-value_percentile-context | 0.040781 → 0.026999 | 0.030431 → 0.019277 | 0.017772 → 0.009316 | 8.041072 → 8.777776 | 3239 → 2977 | 54 → 54 |
| long-hot-group-value_percentile-plain | 0.048521 → 0.018270 | 0.038601 → 0.011962 | 0.017866 → 0.008768 | 8.450976 → 4.263168 | 1861 → 1595 | 28 → 28 |
| long-hot-group-value_percentile-context | 0.069719 → 0.025482 | 0.057969 → 0.017914 | 0.029612 → 0.012083 | 8.292048 → 4.628992 | 3247 → 2981 | 54 → 54 |
| real-many-groups-value_percentile-plain | 0.036587 → 0.017486 | 0.027742 → 0.010503 | 0.010426 → 0.009095 | 4.319872 → 4.264208 | 1857 → 1622 | 28 → 28 |
| real-many-groups-value_percentile-context | 0.063331 → 0.025348 | 0.055520 → 0.017948 | 0.021739 → 0.016058 | 8.372496 → 8.223648 | 3243 → 3008 | 54 → 54 |
| real-hot-group-value_percentile-plain | 0.025087 → 0.018437 | 0.017638 → 0.010227 | 0.013427 → 0.008242 | 8.302176 → 4.362320 | 1861 → 1624 | 28 → 28 |
| real-hot-group-value_percentile-context | 0.044169 → 0.030905 | 0.034020 → 0.021985 | 0.026635 → 0.009656 | 8.615472 → 4.533216 | 3247 → 3010 | 54 → 54 |

## Exact threshold count prefilter versus direct selection

The prefilter counts values below and at most the threshold. At nearest rank r, value < x iff BelowThreshold >= r, and value <= x iff AtMostThreshold >= r; combining these gives exact equality, inequality and all strict comparisons. The filter runs before the native sort and still selects and returns the actual native metric. Both strategies pass the same literal comparison/empty/tie cases and workload expected multisets.

**12/12 strategy samples PASS**, four matched workloads × three repetitions. These use 10,000 scalar inputs: 100 groups × 100 source timestamps with a constant value per group. Each group has 5,050 exact candidate window values (505,000 total). Threshold 90 selects 10% of groups and threshold 10 selects 90%; no optimizer estimate of the qualifying rate is available to conversion. These cases measure the alternative directly rather than treating a threshold-only predicate as a metric replacement.

| Qualifying groups | Strategy | Wall seconds | Engine seconds | CPU seconds | Peak MB | Bytes | Lines |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 90% | direct | 0.303085 | 0.245632 | 0.241061 | 127.855360 | 1578 | 28 |
| 90% | prefilter | 0.291160 | 0.237457 | 0.221949 | 128.761488 | 1813 | 29 |
| 10% | direct | 0.259069 | 0.245748 | 0.234088 | 127.854848 | 1578 | 28 |
| 10% | prefilter | 0.147506 | 0.135335 | 0.126276 | 96.161456 | 1813 | 29 |

Default selection remains direct. The prefilter reduced median wall time by about 43% at a 10% qualifying rate; at 90% the reduction was about 4%, with peak memory increasing slightly. Conversion has no data-selectivity metadata, and the additional threshold counts/comparison work has not been benchmarked across real and decimal thresholds. The smaller general query therefore remains the default; the measured low-selectivity improvement is recorded as a workload-specific opportunity, not silently generalized to every percentile rule.

Long many-group context peak memory increased from 8.04 to 8.78 MB despite lower elapsed time and CPU. These emulator populations and repeated samples include compilation/planning and transport costs. They establish exact outputs and workload-specific measurements, not production throughput, a latency SLA, or uniform memory improvement. Intermediate row growth is calculated from the fixed workload, not a captured per-operator engine profile. Final integrated runtime/performance and shared corpus regeneration remain #49 gates.
