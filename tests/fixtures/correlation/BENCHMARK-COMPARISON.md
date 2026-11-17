# Final integrated correlation comparison (#49)

> Update for #51: the source-shaping revision reran all 64 workloads six times
> on the same engine/image and 12 GiB limits. All 384 samples passed exact result
> and schema assessment. Median per-workload ratios versus the final candidate
> below were 0.928 client elapsed, 0.934 engine elapsed, 0.925 total CPU, and
> 1.000 peak memory; no material regression was confirmed. The exact hashes,
> samples, raw frames, and query-size tradeoff are preserved in the
> [source-shaping evidence](runtime-evidence/source-shaping-final/README.md).

**All 64 candidate workloads passed all three repetitions (192/192 samples).**
The final 255-fixture correctness corpus also passed successful uncancelled
completion with exact empty multiset/schema diagnostics. Candidate production and
corpus revision: `59c455b5a5b008c042727eaef06f92c0da832044`. The benchmark manifest SHA-256 is
`0d4ff06d9875013473afa417bcc4ba8f97e711897bb8348e119aaeea70c4c3cf`. Publication independently rechecks every raw
candidate response, native schema and expected multiset, every executed request
hash, frozen query/fixture/manifest hash, engine version and resource/request
settings. All 64 input/oracle/schema JSON files are byte-identical to the pinned
original baseline. Neither baseline queries nor its original failure ledgers
were regenerated or overwritten.

The [original baseline](BENCHMARK-BASELINE.md) has 195 preserved attempts:
28 plain workloads have three verified passing samples, while 36 context/chain
workloads failed under the original deadlines. Three original sparse-sum schema
failures were separately reassessed using unchanged raw frames under the explicit
boxed-dynamic migration. An interrupted attempt remains failed/unverified.
Original failures remain failures. No speedup ratio is calculated for a workload
without a correctly completed baseline. The new public `ContextComplete:bool`
is mandatory for candidate context output and true for these below-limit inputs;
old dynamic Host/ValueSum/ValuePercentile output is an explicit schema migration.

## Matched conditions and limits

Both original and final 64 use engine `2026.11.15.0741-2645-8e3387b-master`,
immutable image `sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8`,
container/query memory and container swap 12,884,901,888 bytes (12 GiB), no Docker
CPU quota, result-cache age zero, and 15-second client / 13-second server limits.
The candidate captures its own engine version and container inspection. Runs were
segmented, with baseline batches interleaved among correctness work. Planning,
compilation and operating-system warm state were not reset; order was not a
randomized crossover experiment. Three samples support the observed medians,
without statistical-significance or universal latency claims.

Inputs are small synthetic datatables: four sparse, 24 dense, 96 across twelve
groups, or 96 in one hot group. They cover all seven types, repeated/unique values,
timestamp ties, three/four-step order, low/high qualification, retained context,
and mixed multilevel chains. Several baseline costs are dominated by planning.
These results demonstrate substantial measured query/planning improvement on
these workloads; they do not establish production throughput or capacity. Large
hot-window numeric/context pairing can still grow quadratically. Per-operator
runtime cardinalities were not exposed: intermediate growth is unobserved here,
and textual operator counts are not execution profiles.

All 28 correctly completed baseline plain workloads have lower final client,
engine, total CPU and planning CPU medians. Execution CPU increases in three tiny
sparse cases (average, percentile, temporal), and peak memory increases in four
average/percentile cases; the values are retained below. The 36 original
non-completing context/chain workloads now complete correctly within the same
fixed deadlines. No memory-exhaustion diagnosis follows from old absent/invalid
responses. No universal improvement on every resource measure is claimed.

## Every workload: original → final medians

CPU columns are time, not wall time. Memory is exact bytes. Each column's median
is calculated independently. `—` means no verified original statistic; its timeout
is not substituted as elapsed time. Ratios divide verified original client median
by final client median only. Full samples, query lines and operator maps are in
[summary.json](runtime-evidence/integrated-benchmark-final/summary.json).

| Workload | Original result → final | Query bytes | Client s | Engine s | Total CPU s | Peak bytes | Client ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sparse-event_count-plain` | PASS(3) → PASS(3) | 14935 → 956 | 0.457227 → 0.013031 | 0.449039 → 0.005961 | 0.347419 → 0.003159 | 15665856 → 1575360 | 35.09× |
| `sparse-event_count-context` | FAIL(3) → PASS(3) | 50252 → 2337 | — → 0.020725 | — → 0.013377 | — → 0.009944 | — → 8068928 | — |
| `sparse-value_count-plain` | PASS(3) → PASS(3) | 15210 → 1677 | 0.633112 → 0.015378 | 0.625275 → 0.008847 | 0.525523 → 0.007303 | 3710400 → 2228688 | 41.17× |
| `sparse-value_count-context` | FAIL(3) → PASS(3) | 50689 → 3058 | — → 0.022271 | — → 0.015654 | — → 0.012173 | — → 8062544 | — |
| `sparse-value_sum-plain` | PASS(3) → PASS(3) | 16140 → 947 | 0.716113 → 0.011533 | 0.707976 → 0.004899 | 0.360156 → 0.004775 | 3677968 → 1575360 | 62.09× |
| `sparse-value_sum-context` | FAIL(3) → PASS(3) | 51813 → 2326 | — → 0.018452 | — → 0.012185 | — → 0.011784 | — → 8070224 | — |
| `sparse-value_avg-plain` | PASS(3) → PASS(3) | 14581 → 890 | 0.510836 → 0.012604 | 0.501275 → 0.005984 | 0.380330 → 0.005615 | 3679488 → 5095728 | 40.53× |
| `sparse-value_avg-context` | FAIL(3) → PASS(3) | 49870 → 2269 | — → 0.019198 | — → 0.012645 | — → 0.011834 | — → 8187696 | — |
| `sparse-value_percentile-plain` | PASS(3) → PASS(3) | 16065 → 1577 | 0.689807 → 0.016171 | 0.681628 → 0.008756 | 0.567047 → 0.007813 | 3734576 → 4464000 | 42.66× |
| `sparse-value_percentile-context` | FAIL(4) → PASS(3) | 51952 → 2963 | — → 0.023475 | — → 0.016258 | — → 0.012536 | — → 7984384 | — |
| `sparse-temporal-plain` | PASS(3) → PASS(3) | 14980 → 1900 | 0.884424 → 0.031313 | 0.868779 → 0.024940 | 0.660614 → 0.023775 | 9464448 → 1597232 | 28.24× |
| `sparse-temporal-context` | FAIL(3) → PASS(3) | 51345 → 3613 | — → 0.058334 | — → 0.051184 | — → 0.040228 | — → 1965648 | — |
| `sparse-temporal_ordered-plain` | PASS(3) → PASS(3) | 17209 → 2097 | 1.168381 → 0.034085 | 1.151016 → 0.026640 | 0.913977 → 0.026014 | 3717472 → 1591040 | 34.28× |
| `sparse-temporal_ordered-context` | FAIL(3) → PASS(3) | 58423 → 5595 | — → 0.182284 | — → 0.175566 | — → 0.160000 | — → 1957824 | — |
| `sparse-mixed-multilevel-plain` | FAIL(3) → PASS(3) | 67306 → 1923 | — → 0.013786 | — → 0.007016 | — → 0.006884 | — → 3410576 | — |
| `sparse-mixed-multilevel-context` | FAIL(3) → PASS(3) | 278858 → 7726 | — → 0.524817 | — → 0.518058 | — → 0.507537 | — → 26610976 | — |
| `dense-event_count-plain` | PASS(3) → PASS(3) | 14935 → 956 | 0.921713 → 0.010981 | 0.908933 → 0.004702 | 0.664169 → 0.004547 | 15711824 → 1575392 | 83.94× |
| `dense-event_count-context` | FAIL(3) → PASS(3) | 50252 → 2337 | — → 0.019149 | — → 0.012567 | — → 0.011225 | — → 8064240 | — |
| `dense-value_count-plain` | PASS(3) → PASS(3) | 15210 → 1677 | 1.420511 → 0.013950 | 1.403187 → 0.007361 | 1.180332 → 0.006876 | 3725568 → 2228688 | 101.83× |
| `dense-value_count-context` | FAIL(3) → PASS(3) | 50689 → 3058 | — → 0.022707 | — → 0.016199 | — → 0.015479 | — → 8066288 | — |
| `dense-value_sum-plain` | PASS(3) → PASS(3) | 16140 → 947 | 1.375207 → 0.011262 | 1.359966 → 0.004618 | 0.722774 → 0.004441 | 3733120 → 1575392 | 122.11× |
| `dense-value_sum-context` | FAIL(3) → PASS(3) | 51813 → 2326 | — → 0.018001 | — → 0.011327 | — → 0.001724 | — → 8060144 | — |
| `dense-value_avg-plain` | PASS(3) → PASS(3) | 14581 → 890 | 0.907085 → 0.012176 | 0.891338 → 0.005787 | 0.687146 → 0.005440 | 3703120 → 4619296 | 74.50× |
| `dense-value_avg-context` | FAIL(3) → PASS(3) | 49870 → 2269 | — → 0.018986 | — → 0.012696 | — → 0.011510 | — → 8181936 | — |
| `dense-value_percentile-plain` | PASS(3) → PASS(3) | 16065 → 1577 | 1.294812 → 0.015573 | 1.269037 → 0.008934 | 1.048326 → 0.007881 | 3694992 → 4460848 | 83.14× |
| `dense-value_percentile-context` | FAIL(3) → PASS(3) | 51952 → 2963 | — → 0.026032 | — → 0.017879 | — → 0.013742 | — → 8005072 | — |
| `dense-temporal-plain` | PASS(3) → PASS(3) | 14980 → 1900 | 0.996850 → 0.034699 | 0.973207 → 0.026963 | 0.713874 → 0.024442 | 7441344 → 2118752 | 28.73× |
| `dense-temporal-context` | FAIL(3) → PASS(3) | 51345 → 3613 | — → 0.059441 | — → 0.050326 | — → 0.047092 | — → 8708832 | — |
| `dense-temporal_ordered-plain` | PASS(3) → PASS(3) | 17209 → 2097 | 1.244136 → 0.033439 | 1.231387 → 0.026648 | 1.011477 → 0.026417 | 3743072 → 1599616 | 37.21× |
| `dense-temporal_ordered-context` | FAIL(4) → PASS(3) | 58423 → 5595 | — → 0.177750 | — → 0.171130 | — → 0.160868 | — → 12239072 | — |
| `dense-mixed-multilevel-plain` | FAIL(3) → PASS(3) | 67306 → 1923 | — → 0.013926 | — → 0.007076 | — → 0.007069 | — → 3802912 | — |
| `dense-mixed-multilevel-context` | FAIL(3) → PASS(3) | 278858 → 7726 | — → 0.555889 | — → 0.547245 | — → 0.535243 | — → 26671920 | — |
| `many-groups-event_count-plain` | PASS(3) → PASS(3) | 14935 → 956 | 0.959476 → 0.014117 | 0.946328 → 0.006340 | 0.715144 → 0.004870 | 15313808 → 1575328 | 67.97× |
| `many-groups-event_count-context` | FAIL(3) → PASS(3) | 50252 → 2337 | — → 0.021317 | — → 0.010768 | — → 0.009386 | — → 8071248 | — |
| `many-groups-value_count-plain` | PASS(3) → PASS(3) | 15210 → 1677 | 1.381189 → 0.014910 | 1.363470 → 0.007954 | 1.176364 → 0.001792 | 5682144 → 1707120 | 92.64× |
| `many-groups-value_count-context` | FAIL(3) → PASS(3) | 50689 → 3058 | — → 0.024963 | — → 0.017576 | — → 0.016088 | — → 8624352 | — |
| `many-groups-value_sum-plain` | PASS(3) → PASS(3) | 16140 → 947 | 1.377050 → 0.012983 | 1.357789 → 0.005233 | 0.697969 → 0.004987 | 5321632 → 1575328 | 106.07× |
| `many-groups-value_sum-context` | FAIL(3) → PASS(3) | 51813 → 2326 | — → 0.021464 | — → 0.013770 | — → 0.009144 | — → 8074080 | — |
| `many-groups-value_avg-plain` | PASS(3) → PASS(3) | 14581 → 890 | 0.904245 → 0.012975 | 0.885113 → 0.006253 | 0.669590 → 0.005704 | 5116624 → 4534240 | 69.69× |
| `many-groups-value_avg-context` | FAIL(3) → PASS(3) | 49870 → 2269 | — → 0.020703 | — → 0.013527 | — → 0.011493 | — → 8185296 | — |
| `many-groups-value_percentile-plain` | PASS(3) → PASS(3) | 16065 → 1577 | 1.349818 → 0.016226 | 1.337753 → 0.009330 | 1.126581 → 0.008463 | 5376992 → 4460592 | 83.19× |
| `many-groups-value_percentile-context` | FAIL(3) → PASS(3) | 51952 → 2963 | — → 0.026808 | — → 0.019232 | — → 0.007752 | — → 8644784 | — |
| `many-groups-temporal-plain` | PASS(3) → PASS(3) | 14980 → 1900 | 1.003871 → 0.033167 | 0.987365 → 0.025739 | 0.766347 → 0.025514 | 6444864 → 2118032 | 30.27× |
| `many-groups-temporal-context` | FAIL(3) → PASS(3) | 51345 → 3613 | — → 0.060072 | — → 0.053157 | — → 0.043540 | — → 8698624 | — |
| `many-groups-temporal_ordered-plain` | PASS(3) → PASS(3) | 17209 → 2097 | 1.247711 → 0.033381 | 1.228712 → 0.026129 | 0.987242 → 0.025855 | 5403712 → 1591936 | 37.38× |
| `many-groups-temporal_ordered-context` | FAIL(3) → PASS(3) | 58423 → 5595 | — → 0.193440 | — → 0.184530 | — → 0.166898 | — → 11136928 | — |
| `many-groups-mixed-multilevel-plain` | FAIL(3) → PASS(3) | 67306 → 1923 | — → 0.015328 | — → 0.008158 | — → 0.007974 | — → 3935008 | — |
| `many-groups-mixed-multilevel-context` | FAIL(3) → PASS(3) | 278858 → 7726 | — → 0.523297 | — → 0.516305 | — → 0.504605 | — → 26705520 | — |
| `hot-group-event_count-plain` | PASS(3) → PASS(3) | 14939 → 960 | 1.063568 → 0.011680 | 1.046593 → 0.004929 | 0.805280 → 0.004776 | 15313808 → 1575328 | 91.06× |
| `hot-group-event_count-context` | FAIL(3) → PASS(3) | 50260 → 2341 | — → 0.018592 | — → 0.011226 | — → 0.003310 | — → 1574240 | — |
| `hot-group-value_count-plain` | PASS(3) → PASS(3) | 15214 → 1681 | 1.359331 → 0.016949 | 1.344118 → 0.008104 | 1.134762 → 0.008160 | 5521664 → 2228688 | 80.20× |
| `hot-group-value_count-context` | FAIL(3) → PASS(3) | 50697 → 3062 | — → 0.021490 | — → 0.015451 | — → 0.014766 | — → 2228688 | — |
| `hot-group-value_sum-plain` | PASS(3) → PASS(3) | 16148 → 951 | 1.301927 → 0.011778 | 1.288318 → 0.005009 | 0.676452 → 0.004843 | 5070800 → 1575328 | 110.54× |
| `hot-group-value_sum-context` | FAIL(3) → PASS(3) | 51829 → 2330 | — → 0.017810 | — → 0.011222 | — → 0.010567 | — → 1575328 | — |
| `hot-group-value_avg-plain` | PASS(3) → PASS(3) | 14585 → 894 | 0.879130 → 0.012718 | 0.860856 → 0.006290 | 0.660863 → 0.005549 | 5078608 → 4080032 | 69.12× |
| `hot-group-value_avg-context` | FAIL(3) → PASS(3) | 49878 → 2273 | — → 0.019239 | — → 0.011375 | — → 0.010020 | — → 4404864 | — |
| `hot-group-value_percentile-plain` | PASS(3) → PASS(3) | 16073 → 1581 | 1.329915 → 0.019129 | 1.315029 → 0.009879 | 1.103986 → 0.007886 | 5253344 → 4361680 | 69.52× |
| `hot-group-value_percentile-context` | FAIL(3) → PASS(3) | 51968 → 2967 | — → 0.025255 | — → 0.018556 | — → 0.016698 | — → 4631248 | — |
| `hot-group-temporal-plain` | PASS(3) → PASS(3) | 15334 → 2339 | 0.951625 → 0.036742 | 0.934882 → 0.029982 | 0.724390 → 0.023897 | 6576176 → 1603984 | 25.90× |
| `hot-group-temporal-context` | FAIL(3) → PASS(3) | 52402 → 4230 | — → 0.071086 | — → 0.063428 | — → 0.059415 | — → 8538736 | — |
| `hot-group-temporal_ordered-plain` | PASS(3) → PASS(3) | 18763 → 2893 | 1.483154 → 0.041237 | 1.467434 → 0.034299 | 1.246881 → 0.027134 | 5582272 → 1597792 | 35.97× |
| `hot-group-temporal_ordered-context` | FAIL(3) → PASS(3) | 62590 → 7514 | — → 0.391483 | — → 0.384629 | — → 0.361008 | — → 12735408 | — |
| `hot-group-mixed-multilevel-plain` | FAIL(3) → PASS(3) | 67318 → 2313 | — → 0.018697 | — → 0.012249 | — → 0.012425 | — → 4722672 | — |
| `hot-group-mixed-multilevel-context` | FAIL(3) → PASS(3) | 278882 → 7730 | — → 0.550816 | — → 0.543517 | — → 0.521923 | — → 16900512 | — |

## Planning and execution CPU

These 28 rows have correctly completed originals; candidate CPU breakdowns for
all 64 workloads remain in the structured samples. Column medians need not add
exactly to median total CPU. Three sparse execution-CPU increases coexist with
much lower planning and total CPU; volatile millisecond samples do not establish
an execution-only capacity improvement.

| Plain workload | Planning CPU s, original → final | Execution CPU s, original → final |
| --- | ---: | ---: |
| `sparse-event_count-plain` | 0.342688 → 0.002048 | 0.002161 → 0.000811 |
| `sparse-value_count-plain` | 0.520421 → 0.002513 | 0.005266 → 0.002219 |
| `sparse-value_sum-plain` | 0.353632 → 0.000278 | 0.006524 → 0.004539 |
| `sparse-value_avg-plain` | 0.375144 → 0.001517 | 0.001888 → 0.005494 |
| `sparse-value_percentile-plain` | 0.562852 → 0.005018 | 0.004195 → 0.005112 |
| `sparse-temporal-plain` | 0.658663 → 0.017616 | 0.001951 → 0.002705 |
| `sparse-temporal_ordered-plain` | 0.911212 → 0.021399 | 0.002765 → 0.000000 |
| `dense-event_count-plain` | 0.651796 → 0.000344 | 0.009451 → 0.002066 |
| `dense-value_count-plain` | 1.161004 → 0.001320 | 0.013666 → 0.005340 |
| `dense-value_sum-plain` | 0.700512 → 0.000868 | 0.022262 → 0.003352 |
| `dense-value_avg-plain` | 0.660983 → 0.000000 | 0.019438 → 0.005197 |
| `dense-value_percentile-plain` | 1.042882 → 0.000000 | 0.016522 → 0.007881 |
| `dense-temporal-plain` | 0.707781 → 0.021460 | 0.014767 → 0.002335 |
| `dense-temporal_ordered-plain` | 0.992920 → 0.021241 | 0.017739 → 0.004678 |
| `many-groups-event_count-plain` | 0.695835 → 0.000000 | 0.019543 → 0.004804 |
| `many-groups-value_count-plain` | 1.148484 → 0.000000 | 0.039189 → 0.000145 |
| `many-groups-value_sum-plain` | 0.658364 → 0.000000 | 0.039605 → 0.004825 |
| `many-groups-value_avg-plain` | 0.639988 → 0.000000 | 0.029592 → 0.005670 |
| `many-groups-value_percentile-plain` | 1.086873 → 0.000521 | 0.039708 → 0.008463 |
| `many-groups-temporal-plain` | 0.736568 → 0.021418 | 0.030532 → 0.004096 |
| `many-groups-temporal_ordered-plain` | 0.947173 → 0.019776 | 0.037722 → 0.010166 |
| `hot-group-event_count-plain` | 0.785896 → 0.000000 | 0.027197 → 0.004639 |
| `hot-group-value_count-plain` | 1.099280 → 0.005354 | 0.029607 → 0.004673 |
| `hot-group-value_sum-plain` | 0.646249 → 0.001791 | 0.021296 → 0.002947 |
| `hot-group-value_avg-plain` | 0.623393 → 0.000000 | 0.019681 → 0.004516 |
| `hot-group-value_percentile-plain` | 1.074974 → 0.001569 | 0.029012 → 0.007530 |
| `hot-group-temporal-plain` | 0.694536 → 0.023897 | 0.029854 → 0.000000 |
| `hot-group-temporal_ordered-plain` | 1.206969 → 0.027134 | 0.029719 → 0.000000 |

## Separately labeled strategy studies

These studies support algorithm choices with their own exact frozen intermediate
revisions. Their old strategies are generally post-scalar foundations, rather
than the original `07525ee` baseline. Do not combine unlike populations, deadlines
or resource provenance into one speedup claim. All use the same recorded engine
version and 12 GiB query limits; numeric studies explicitly reuse preceding-lane
container snapshots rather than recapturing each run's inspection.

| Study | Population / comparisons | Client / server / cache settings | Evidence and observed limitations |
| --- | --- | --- | --- |
| Native value-count intervals | 500 repeated/unique observations; 12 paired samples | 45s / not explicitly set / not explicitly set by runtime harness | [Report](VALUE-COUNT.md), [all raw queries/responses](runtime-evidence/value-count-strategy-raw/README.md). Lower measured engine/CPU/peak in both profiles; per-operator growth unobserved. |
| Native sums | 96 rows, long/real × grouped/hot × context off/on; 48 samples | 20s / 18s / zero cache age | [Report and raw evidence](runtime-evidence/native-sums/README.md). All elapsed medians fall; plain long hot CPU and three memory medians rise. |
| Native averages | Same 96-row crossed profiles; 48 samples | 45s / 43s / zero cache age | [Report and raw evidence](runtime-evidence/native-averages/README.md). All elapsed medians fall; long grouped context CPU and real grouped plain peak rise. |
| Native exact percentiles | Same 96-row profiles; 48 paired samples, plus 12 separate prefilter samples | 45s / 43s / zero cache age | [Report and raw evidence](runtime-evidence/native-percentiles/README.md). Long grouped-context peak rises. Separate 10k-input/505k-derived-candidate direct/prefilter cases show selectivity-dependent gains; high qualification peak rises. The default remains direct. |
| Ordered contributors | 3/5/7 positions, branching 3 and 5 positions/branching 6; 48 old/final samples | 15s / 13s / zero cache age | [Report and raw evidence](TEMPORAL-PARTICIPANTS.md). Context elapsed/CPU/peak fall; unchanged no-context query hashes are noise controls. Separate actual growth diagnostics count 60 pairs vs 2,187 full tuples and 165 vs 7,776, with scalar threshold relations only 3/6 rows. |
| Existence-only child count | 38,400 grouped, 30,000 hot/threshold-one, 6,400 sparse inputs; 24 samples | 45s / 43s / zero cache age | [Report and all raw evidence](runtime-evidence/count-existence-performance/README.md). All elapsed medians fall; sparse total CPU, hot planning CPU, threshold-one execution CPU and threshold-one peak rise. Own container inspection retained. |

The percentile candidate population is mathematically derived from its frozen
inputs, not an observed operator profile. Ordered growth diagnostics are actual
separate engine counts, not elapsed measurements. Larger synthetic existence and
prefilter cases add execution evidence but do not establish all-type production
capacity. Source pairing and bounded display construction remain real costs.

## Failure preservation and reproduction

The initial integrated candidate completed 189/192 samples and failed all three
hot-group plain-chain attempts with HTTP 400. Its unsupported `prev(...,9999)`
was reduced to an engine `SEM0248` restriction of offsets to 1–1024. The planner
now retains general exact counts above threshold 1,025. The positive regression
checks qualifying and one-short populations at 1,025, 1,026 and 10,000; the final
255 corpus and full 192 samples all pass after the fix. The
[initial candidate](runtime-evidence/integrated-candidate-initial/summary.json),
[raw failure/boundary proof](runtime-evidence/predecessor-offset-limit/README.md),
and initial 254-fixture passing checkpoint remain archived separately. They are
not merged into the final passing ledger or erased.

[Final benchmark evidence](runtime-evidence/integrated-benchmark-final/README.md)
contains the exact frozen 64 manifest, inputs, independent oracles, generated KQL,
raw requests/responses/transport records, engine/container snapshots, per-attempt
reassessment and checksums. [Final corpus evidence](runtime-evidence/integrated-acceptance-final/README.md)
contains all 255 executed KQL/expected comparisons and strict completion proof.
The baseline's original raw 195 attempts remain in its existing immutable archive.

To reproduce publication offline after extracting the archived directories:

```sh
python -m tests.fixtures.correlation.benchmark_comparison \
  --manifest /path/to/frozen-candidate/manifest.json \
  --evidence /path/to/kusto-spec34-final-benchmark-fixed \
  --output /tmp/reproduced-comparison
```

The range-parser dependency #31 and outstanding #32 acceptance remain separate.
This work introduces no local range parser, new partial ordered-threshold
semantics, issue closure, or production capacity promise.
