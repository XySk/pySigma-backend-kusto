# Context transport review fixes

Production still retains the #52 summary/evidence union. The earlier paired
performance decision remains unchanged; this supplemental evidence corrects the
#53 growth model and proves the corrected separate-transport candidate rather
than inferring correctness from the scale diagnostics.

## Complete transport growth

`transport-growth-manifest.json` records every context-bearing relation and
every parent/public transport use for all 15 scale workloads. The old estimate
counted only `ScaleChild`, which made depth-one, depth-three, and shared graphs
look identical. The corrected totals include two relations for depth one and
four for depth three/shared use:

| Workload | Relations | Current rows / estimated bytes | Candidate rows / estimated bytes |
| --- | ---: | ---: | ---: |
| Chain depth one | 2 | 17,142 / 1,093,871 | 8,887 / 300,717 |
| Chain depth three | 4 | 34,662 / 2,325,103 | 26,407 / 1,123,267 |
| Shared child consumers | 4 | 43,014 / 2,609,743 | 34,759 / 1,376,689 |

These are transparent row/serialized-byte models, not engine operator
cardinalities. The Kusto response protocol still does not expose per-operator
cardinality.

## Corrected candidate correctness

The corrected separate summary/evidence candidate passed 19/19 focused queries
on Kusto `2026.11.15.0741-2645-8e3387b-master`. The matrix covers direct output,
shared and multilevel graphs, callbacks, 999/1000/1001 display populations,
values hidden from displays but consumed by a parent, cross-field relationships,
Boolean/ordered alternatives, numeric consumers, and stable IDs across every
summary/evidence consumer. The restored production union then passed the same
19/19 gate.

Two callback corrections are retained rather than overwritten:

- Query `41b6492125f5ae404149b6865643857876fefc04f8e23378b9ef7b201f7ffb0f`
  failed with `SYN0002` because an independently defined evidence query was
  nested directly inside a join expression.
- Query `c9a00b4ae1d4c51904aa354586d2be563085c4a0d1db4fcbe51c53f98ff3a570`
  failed with `SEM0100` because callback projection removed the private match
  ID before a later consumer.

The successful candidate derives IDs from window/group identity after callback
projection and joins eligible summaries to complete evidence through separately
bound relations. Its exact temporary source patch is archived with the frozen
queries, manifests, requests, responses, ledgers, engine version, and both failed
attempts in `raw-evidence.tar.gz`; `raw-files.json` indexes every archived byte.
`summary.json` is the machine-readable verification report, and `SHA256SUMS`
authenticates the published files.
