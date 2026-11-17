# Separate context transport prototype (#53)

The separate child-summary/complete-evidence prototype was **rejected**.  The
production generator retains the #52 summary/evidence union and stable shared
`CorrelationMatchId` assignment.

## Paired scale result

The baseline manifest SHA-256 is
`078076e14150e66c798006c7e5e0ad33803f2e89ed09e02f1b010c8ec9a1ed41` and the
candidate manifest SHA-256 is
`a395a58a94217468e6eb510c58f799fb776ed455f048313215853e769d4eef98`.
All 75/75 baseline samples and 75/75 candidate samples passed exact independent
multiset diagnostics, with five samples for each of 15 workloads.

The matrix independently varies density and input size, hot versus many-group
distribution, repeated versus unique values, qualifying selectivity, one versus
three requested context fields and relationship fan-out, shared child consumers,
chain depth, and numeric/context pairing.  `summary.json` records exact workload
and query hashes, modeled transport rows/bytes, query bytes and textual operator
counts, CPU, peak memory, client/engine elapsed time, and failures.  Per-operator
runtime cardinality was not exposed by the response frames and is recorded as
unavailable rather than inferred from query text.

The candidate cleared the 15% CPU target in seven workloads, including 92.2%
lower median CPU for depth three and 70.4% lower for shared child consumers.
However, dense/hot 256-row workloads used 35.9–48.5% more CPU, and representative
peak memory increased by 65–152% in most workloads.  Those confirmed regressions
exceed the 10% guard, so the candidate is not eligible for adoption.

## Failed prototype

The initial prototype retained the union-era `project-away` step even though a
separate summary relation no longer carried null evidence columns.  Kusto rejected
the exact callback query hash
`1c8f09dfa646398dd7662e6ab68e1ef48877e39910b7e2b2efc11618728f2ce8` and complete
parent query hash
`dc8532b5c430781bc1ae09255c9d132871daef0d8abc3a06710f0ac2f07df65c` with
`SEM0104`.  The raw requests and responses are retained.  Removing that obsolete
projection produced the successful candidate measured above; it did not make the
candidate pass the adoption gate.

## Environment and files

Runs used Kusto `2026.11.15.0741-2645-8e3387b-master`, immutable image
`sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8`,
12,884,901,888-byte container/query memory and swap, no CPU quota, disabled query
result cache, and 30-second client / 28-second server limits.

- `summary.json` is the verified comparison and adoption decision.
- `final-gates.json` records the final 255/255 strict fixture and 64/64 broad
  workload passes; `final-gates-raw-evidence.tar.gz` and its index retain their
  frozen queries, manifests, requests, responses, and ledgers.
- `raw-files.json` authenticates every archived fixture, query, ledger, request,
  response, transport record, and failed prototype.
- `raw-evidence.tar.gz` contains those exact artifacts.
- `SHA256SUMS` authenticates the published files.

The [review-fix supplement](../context-transport-review-fixes/README.md) corrects
the child-only transport-growth estimate and publishes the missing successful
corrected-candidate focused correctness evidence. It does not change this paired
performance rejection or production strategy.
