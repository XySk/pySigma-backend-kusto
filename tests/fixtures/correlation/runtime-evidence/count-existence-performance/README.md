# Existence-only child count strategy measurements

All 24 samples passed successful uncancelled completion, native public schema,
and exact independently expected result multisets: four profiles, two plans,
three repetitions. Final publication independently re-assesses every unchanged
raw response with the strict harness and validates every executed request/query,
fixture and manifest hash. The measured production revision is
`55b07d4ce1cb892e812afa232dbd907b77dfe364`. These are paired general versus
specialized count strategies after scalar/context foundations, separate from
the original pre-redesign 64-workload baseline and the final integrated candidate.

Engine `2026.11.15.0741-2645-8e3387b-master`, immutable image
`sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8`,
container/query memory and container swap 12,884,901,888 bytes, no explicit Docker
CPU quota. The run captures its own container inspection and engine version.
Result cache age is zero; client/server deadlines are 45/43 seconds on both sides.
This larger study has different deadlines from the 15/13-second original/final
64-workload comparison. Cache disabled does not mean cold planning, compilation,
or operating-system state. Plan order was not randomized.

The four populations have 38,400 rows across 64 groups, 30,000 rows in a hot
group, 6,400 sparse nonqualifying rows, and 30,000 threshold-one rows. Their
frozen query datatables retain exact inputs. Paired oracle JSON files differ
only in `name`; independent expected multisets and native schemas are identical.
Each eligible child keeps individual source-match multiplicity before predecessor
selection. An identity callback requests the observable general plan.

All values below are medians, general → specialized. Client elapsed includes
transport; engine elapsed and CPU come from actual QueryResourceConsumption.
CPU column medians are independent and need not add exactly. Memory is bytes.

| Profile / inputs | Client s | Engine s | Total CPU s | Planning CPU s | Execution CPU s | Peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| many-groups / 38,400 | 0.0977520 → 0.0891100 | 0.0646046 → 0.0542518 | 0.0499330 → 0.0359190 | 0.0176770 → 0.0134240 | 0.0378400 → 0.0224950 | 24020176 → 18945856 |
| hot-group / 30,000 | 0.0807530 → 0.0719790 | 0.0503515 → 0.0429840 | 0.0406640 → 0.0326460 | 0.0159370 → 0.0188540 | 0.0247270 → 0.0175510 | 21759824 → 20949264 |
| sparse / 6,400 | 0.0356540 → 0.0299940 | 0.0282898 → 0.0225474 | 0.0156570 → 0.0208400 | 0.0148640 → 0.0143890 | 0.0019230 → 0.0064620 | 5924496 → 3744848 |
| threshold-one / 30,000 | 0.0778030 → 0.0674930 | 0.0492924 → 0.0396191 | 0.0347150 → 0.0283160 | 0.0183170 → 0.0142370 | 0.0158420 → 0.0164560 | 18631568 → 19032416 |

All four elapsed medians decrease. Total CPU decreases in three profiles and
increases in sparse activity (0.015657 → 0.020840 s). Threshold-one peak memory
increases (18,631,568 → 19,032,416 bytes), as does its execution CPU
(0.015842 → 0.016456 s). Hot-group planning CPU also increases. These exceptions
rule out a blanket claim that every resource measure improves. Three samples
support these observed medians, not statistical significance or a universal SLA.
No per-operator runtime cardinalities were captured; intermediate growth remains
unobserved. Synthetic emulator datatables do not establish production capacity.

[summary.json](summary.json) retains every per-profile metric sample and query
size/operator structure. [attempts.json](attempts.json) records independent
raw-response reassessment. [raw-evidence.tar.gz](raw-evidence.tar.gz) preserves
the exact frozen manifest, queries/oracles, every raw request/response/transport,
engine version, container inspection and original ledger. [raw-files.json](raw-files.json)
and [SHA256SUMS](SHA256SUMS) verify original archive bytes and publication files.
No failure or retry is discarded; this measured run contains no failed attempts.
