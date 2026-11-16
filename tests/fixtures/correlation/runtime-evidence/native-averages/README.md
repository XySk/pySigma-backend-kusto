# Native current-window averages (#43)

Core revision `2a132794d6f5316888522ff771b7a6d56038177e`, based on integrated scalar/count/temporal/context/native-sum work at `40bc40c`. The paired old average is revision `21c7b9b609fde0e3f1ede6391d2d993dbaae436e`, after scalar/context foundations, before the average change. These paired measurements are separate from the original pre-redesign baseline coordinated under #35.

Correctness: **16/16 PASS** against Kusto `2026.11.15.0741-2645-8e3387b-master`, `NetDefaultDB`, localhost:8080. Frozen public-conversion queries and independent literal multiset diagnostics are in `correctness-fixtures`; requests, responses, query hashes, timing, engine version and verdicts are in `correctness`. The response gate requires successful uncancelled completion; empty or missing transport responses never mean success.

Coverage includes all six comparisons and fractional thresholds, empty populations including inequality, finite-only observations and ordinary decimal-to-real average rounding, duplicate source searches and observation weighting, explicit numeric-string normalization, large signed-long averages, inclusive boundaries and one tick outside, overlapping anchors, mapped aliases/mixed sources, scalar child metrics, retained numeric dedup per child/group, complete relationship context, and native typed groups. The expired-large fixture proves exact later average 3 after 1e100 leaves the window, including null/NaN arrival anchors. The separate empty-only and already-expired windows never match.

Paired strategy benchmark: **48/48 samples PASS**, eight matched scalar workloads × three repetitions × old/new. Each workload has 96 inputs, the same independent inclusive-window oracle, and required real `ValueAvg` output; context workloads additionally check exact context collections and `ContextComplete`. Workloads cover long/real input, many groups/one hot group, context off/on, ties and repeated observations. All observed medians decreased in wall and engine time. The paired queries remove runtime type boxes, one anchor projection stage, and the separate usable count. Native current-window aggregation remains necessary to avoid expired floating-point residue; observation rows are preserved without averaging timestamp means.

Per-query memory limit is 12,884,901,888 bytes, timeout 45 seconds, benchmark cache age zero, and all runs used the same existing `kusto-spec-17` emulator container. `preceding-container-inspect.json` was captured by the root immediately preceding reporting/average correctness lanes; it records memory/swap limits and image. It was reused as a resource snapshot, not recaptured by this ticket. Runtime and benchmark results record the actual engine version and request settings.

Each cell is old → new, medians of three successful samples. CPU is the engine-reported total CPU seconds; memory is engine peak bytes divided by 1,000,000. Native queries include no context work when none is requested. Query byte/line counts exclude the datatable input.

| Workload | Wall seconds | Engine seconds | CPU seconds | Peak MB | Bytes | Lines |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| long-many-groups-value_avg-plain | 0.019381 → 0.014181 | 0.011599 → 0.006479 | 0.010480 → 0.003470 | 4.596784 → 4.517376 | 1261 → 904 | 18 → 15 |
| long-many-groups-value_avg-context | 0.024558 → 0.022919 | 0.016814 → 0.015209 | 0.006160 → 0.013272 | 8.208128 → 8.136672 | 2640 → 2283 | 44 → 41 |
| long-hot-group-value_avg-plain | 0.017273 → 0.014845 | 0.008559 → 0.007465 | 0.007125 → 0.006852 | 4.207792 → 4.080432 | 1265 → 908 | 18 → 15 |
| long-hot-group-value_avg-context | 0.023657 → 0.018493 | 0.016921 → 0.011264 | 0.012976 → 0.010043 | 8.572608 → 4.503168 | 2644 → 2287 | 44 → 41 |
| real-many-groups-value_avg-plain | 0.015112 → 0.013221 | 0.008336 → 0.006443 | 0.007314 → 0.005946 | 4.537728 → 4.613840 | 1261 → 904 | 18 → 15 |
| real-many-groups-value_avg-context | 0.023836 → 0.019878 | 0.016413 → 0.012865 | 0.014738 → 0.011938 | 4.519616 → 4.502976 | 2640 → 2283 | 44 → 41 |
| real-hot-group-value_avg-plain | 0.016066 → 0.013572 | 0.008943 → 0.006900 | 0.007010 → 0.006128 | 4.257024 → 4.177296 | 1265 → 908 | 18 → 15 |
| real-hot-group-value_avg-context | 0.023598 → 0.018754 | 0.016703 → 0.011462 | 0.014883 → 0.002202 | 8.502976 → 4.404480 | 2644 → 2287 | 44 → 41 |

These small emulator workloads include planning and compilation costs and establish representative correctness and direction, not production capacity or a latency SLA. CPU for long many-group context increased from 0.006160 to 0.013272 seconds despite lower wall/engine time; the other CPU medians decreased. These small CPU samples are volatile and do not establish improvement on every resource measure. Peak memory fluctuates: real many-group plain increased slightly (4.54 → 4.61 MB); the other measured memory medians decreased. Intermediate candidate-pair counts were not separately observable; the exact-time join remains quadratic in a hot window. Final integrated benchmarks and broader corpus refresh belong to #49.
