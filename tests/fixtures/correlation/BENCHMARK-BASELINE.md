# Pinned pre-redesign scalar baseline (#35)

All 64 frozen workloads were attempted at least three times without client
interruption. **28 plain workloads passed** independent result-multiset and schema
checks in three samples each. All 32 context workloads and four mixed/multilevel
chain workloads failed every non-interrupted attempt under the fixed deadlines.
These failures remain failures; they establish no empty result or speedup ratio.

The six measured ledgers contain 195 attempts: 81 originally recorded PASS and
114 originally recorded FAIL. Three original sparse `value_sum` failures were
schema-only: the old query returns boxed dynamic numeric output. Separate raw-frame
reassessment verifies their exact expected values under the documented baseline
schema exception, bringing verified samples to 84. The original FAIL ledgers are
unchanged. One explicitly interrupted attempt remains FAIL/unverified and is
excluded from the required three repetitions; two other workloads have four
non-interrupted samples after segmented execution. The preliminary two-query
smoke ledger is also preserved separately and excluded from measured counts.

## Identity and measurement conditions

- Backend baseline: `07525ee846878754bda7933eab0684895a156086`.
- [Frozen workload manifest](benchmark-baseline/manifest.json): SHA-256
  `5141df3cfb4f43f276f4ca49f0113a467ec59c97534d042b35f51c928c0963ea`.
  Every original query and input/expected fixture hash was verified unchanged.
- Engine: `2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`,
  existing `kusto-spec-17` container and `NetDefaultDB` through REST v2.
- Image ID: `sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8`.
  The image tag is mutable; this immutable local image ID is the measured identity.
- Container memory and swap limit: 12,884,901,888 bytes each (12 GiB).
  Docker CPU quota and NanoCPUs are both zero: no explicit container CPU cap.
  Per-query memory option is the same 12 GiB; the engine's reported default
  per-iterator limit is 5,368,709,120 bytes. No resource increase or engine
  replacement occurred between segments.
- Measurements ran on 2026-11-16 UTC in six exclusive-engine batches, interleaved
  with other tickets' correctness gates. Each batch records engine/version/resource
  identity. Result caching was disabled. Planning/compilation and other warm state
  were not reset between repetitions or batches.
- Client deadline: 15 seconds; server timeout: 13 seconds. Every measured batch
  used identical request settings. The preliminary smoke omitted server timeout
  and is excluded from the measured comparison.

These are small synthetic datatables: 4 sparse rows, 24 dense rows, 96 rows in
12 small groups, or 96 rows in one hot group. They exercise query planning and
representative scalar behavior, including ties, repeated/mostly unique values,
inclusive endpoints, three/four-step sequences, low/high qualifying rates and
context/chains. They **do not measure production throughput or capacity**. Planning
CPU dominates several successful samples; reporting total elapsed alone would
obscure that distinction. CPU and peak-per-node memory come from actual engine
`QueryResourceConsumption` frames; absent statistics remain null. Intermediate
relation growth was not exposed in these captured frames and is unobserved.
Textual operator counts and input/output counts are not substitutes.

## Verified plain workloads

The table gives medians of the three independently verified repetitions. Full
individual client/engine elapsed, total/planning/execution CPU and peak-memory
samples are retained in the structured summary. CPU figures are CPU time, not
wall time; column medians are calculated independently.

| Workload | Input rows | Expected rows | Client s | Total CPU s | Planning CPU s | Execution CPU s | Peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `sparse-event_count-plain` | 4 | 2 | 0.4572 | 0.3474 | 0.3427 | 0.0022 | 14.94 |
| `sparse-value_count-plain` | 4 | 1 | 0.6331 | 0.5255 | 0.5204 | 0.0053 | 3.54 |
| `sparse-value_sum-plain` | 4 | 3 | 0.7161 | 0.3602 | 0.3536 | 0.0065 | 3.51 |
| `sparse-value_avg-plain` | 4 | 3 | 0.5108 | 0.3803 | 0.3751 | 0.0019 | 3.51 |
| `sparse-value_percentile-plain` | 4 | 3 | 0.6898 | 0.5670 | 0.5629 | 0.0042 | 3.56 |
| `sparse-temporal-plain` | 4 | 0 | 0.8844 | 0.6606 | 0.6587 | 0.0020 | 9.03 |
| `sparse-temporal_ordered-plain` | 4 | 0 | 1.1684 | 0.9140 | 0.9112 | 0.0028 | 3.55 |
| `dense-event_count-plain` | 24 | 23 | 0.9217 | 0.6642 | 0.6518 | 0.0095 | 14.98 |
| `dense-value_count-plain` | 24 | 23 | 1.4205 | 1.1803 | 1.1610 | 0.0137 | 3.55 |
| `dense-value_sum-plain` | 24 | 24 | 1.3752 | 0.7228 | 0.7005 | 0.0223 | 3.56 |
| `dense-value_avg-plain` | 24 | 24 | 0.9071 | 0.6871 | 0.6610 | 0.0194 | 3.53 |
| `dense-value_percentile-plain` | 24 | 24 | 1.2948 | 1.0483 | 1.0429 | 0.0165 | 3.52 |
| `dense-temporal-plain` | 24 | 22 | 0.9969 | 0.7139 | 0.7078 | 0.0148 | 7.10 |
| `dense-temporal_ordered-plain` | 24 | 8 | 1.2441 | 1.0115 | 0.9929 | 0.0177 | 3.57 |
| `many-groups-event_count-plain` | 96 | 84 | 0.9595 | 0.7151 | 0.6958 | 0.0195 | 14.60 |
| `many-groups-value_count-plain` | 96 | 84 | 1.3812 | 1.1764 | 1.1485 | 0.0392 | 5.42 |
| `many-groups-value_sum-plain` | 96 | 96 | 1.3771 | 0.6980 | 0.6584 | 0.0396 | 5.08 |
| `many-groups-value_avg-plain` | 96 | 96 | 0.9042 | 0.6696 | 0.6400 | 0.0296 | 4.88 |
| `many-groups-value_percentile-plain` | 96 | 96 | 1.3498 | 1.1266 | 1.0869 | 0.0397 | 5.13 |
| `many-groups-temporal-plain` | 96 | 72 | 1.0039 | 0.7663 | 0.7366 | 0.0305 | 6.15 |
| `many-groups-temporal_ordered-plain` | 96 | 24 | 1.2477 | 0.9872 | 0.9472 | 0.0377 | 5.15 |
| `hot-group-event_count-plain` | 96 | 0 | 1.0636 | 0.8053 | 0.7859 | 0.0272 | 14.60 |
| `hot-group-value_count-plain` | 96 | 0 | 1.3593 | 1.1348 | 1.0993 | 0.0296 | 5.27 |
| `hot-group-value_sum-plain` | 96 | 0 | 1.3019 | 0.6765 | 0.6462 | 0.0213 | 4.84 |
| `hot-group-value_avg-plain` | 96 | 0 | 0.8791 | 0.6609 | 0.6234 | 0.0197 | 4.84 |
| `hot-group-value_percentile-plain` | 96 | 0 | 1.3299 | 1.1040 | 1.0750 | 0.0290 | 5.01 |
| `hot-group-temporal-plain` | 96 | 47 | 0.9516 | 0.7244 | 0.6945 | 0.0299 | 6.27 |
| `hot-group-temporal_ordered-plain` | 96 | 22 | 1.4832 | 1.2469 | 1.2070 | 0.0297 | 5.32 |

## Failed workloads and preserved evidence

All 36 context/chain workloads failed to deliver verified completed results within
these deadlines, including workloads whose independent expected multiset is
empty. Recorded outcomes are either curl timeouts with zero received bytes or
empty/unparsable responses around the server deadline. The payloads do not
establish memory exhaustion or a particular failing query operator, so neither is
claimed. CPU/memory statistics for these unsuccessful requests are unavailable.
The explicit client interruption and unrecorded in-flight requests left by a lost
agent session are preserved alongside completed attempts; they are not counted as
successful runs. No retry replaced or erased an earlier artifact.

- [Per-workload summary](runtime-evidence/benchmark-baseline/summary.json): all 64
  workloads, input/fixture/query hashes, byte/line/operator structure, output/schema
  expectations, repeated measurements and exact failure details.
- [Attempt index](runtime-evidence/benchmark-baseline/attempts.json): source run and
  repetition for every measured sample, original versus verified status, interruption
  marker and raw-response path.
- [Separate schema reassessment](runtime-evidence/benchmark-baseline/schema-reassessment.json):
  three original boxed-sum failures, original response hashes/schema and independent
  expected-fixture hashes. The preliminary smoke percentile schema failure remains
  in its original diagnostic ledger.
- [Raw archive](runtime-evidence/benchmark-baseline/raw-evidence.tar.gz): 565 original
  files, including all success/failure ledgers, requests, responses, transport records,
  `.show version` replies and container inspection. Archive contents are unchanged
  original bytes; zero-length replies remain zero-length.
- [Per-file raw hashes](runtime-evidence/benchmark-baseline/raw-files.json) and
  [archive/report checksums](runtime-evidence/benchmark-baseline/SHA256SUMS) make
  the preserved evidence independently checkable. The archive also retains the two
  preliminary smoke attempts marked diagnostic-only.

## Reproducing and comparing later strategies

[Baseline procedure](benchmark-baseline/README.md) documents the public ordinary
collection parser, real processing pipeline/partial schema and `KustoBackend.convert`
boundary, independent brute-force inclusive-window oracle, exact schema/multiset
gate, freeze/run commands, and intentional old/new schema differences. Candidate
context-bearing output must additionally contain `ContextComplete:bool` and true
for these below-limit populations; its absence cannot pass candidate acceptance.
No baseline query or dataset should be regenerated with the candidate backend.
Use the same version/image, resources, cache/deadline settings and idle engine;
compare full workloads and repeated samples only after verifying expected output.
For failed baseline workloads report completion versus non-completion rather than
an elapsed speedup ratio. Larger paired scaling studies belong in their own
labeled evidence and must use identical settings for both sides.

The offline report is reproducible after extracting the archive into a directory
with the six original run-directory names:

```bash
python -m tests.fixtures.correlation.benchmark_report \
  --manifest tests/fixtures/correlation/benchmark-baseline/manifest.json \
  --evidence /tmp/kusto-spec34-baseline-measured \
    /tmp/kusto-spec34-baseline-remaining /tmp/kusto-spec34-baseline-fast \
    /tmp/kusto-spec34-baseline-context-batch1 \
    /tmp/kusto-spec34-baseline-context-batch2 \
    /tmp/kusto-spec34-baseline-context-batch3 \
  --diagnostic /tmp/kusto-spec34-baseline-smoke \
  --output /tmp/kusto-baseline-report-new
```

This consolidation makes no engine calls and never rewrites original evidence.
Earlier focused fixture results and runtime gaps in [RUNTIME.md](RUNTIME.md),
mathematical models, fixture-generation tests, and later candidate studies remain
separate evidence. This baseline establishes no blanket correlation acceptance,
throughput or capacity claim.
