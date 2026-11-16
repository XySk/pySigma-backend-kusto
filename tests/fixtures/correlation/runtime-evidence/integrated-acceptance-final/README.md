# Final integrated real-Kusto acceptance

All 255 fixtures passed on 2026-11-16, using production/generator revision
`59c455b5a5b008c042727eaef06f92c0da832044`. Frozen manifest SHA-256:
`d2da8ac461cb3a053970f8993e0b110493367ecda43e608adc2a662c83d74ceb`.
The final publication independently checks every executed request against its
frozen KQL hash and reassesses every unchanged raw response: HTTP 200, exactly one
empty primary comparison result, exactly one successful uncancelled completion,
and no error frames. All 255 complete; no excluded or pending fixture is counted.

Engine `2026.11.15.0741-2645-8e3387b-master`, build `1.0.9815.14081`, database
`NetDefaultDB`, explicit per-query memory 12,884,901,888 bytes, client timeout 60s.
The correctness runtime harness does not explicitly configure server timeout or
result-cache age and does not inspect the container. The immediately following
exclusive-lane final benchmark captures the same engine/image and 12 GiB container
memory/swap directly; that separate provenance is not presented as a runtime
inspection. Correctness elapsed samples are not benchmark comparisons.

The corpus covers all seven types, all 49 singleton producer/consumer smoke edges,
plus rich independent fixtures for exact boundaries/ties/multiplicity, invalid
inputs, native public schemas, mappings/aliases/source predicates, mixed/shared/
multilevel dependencies, document order/generation controls, required metrics,
complete internal context with bounded displays, every successful ordered and
satisfied Boolean participant, empty-evidence child structure, accumulated report
lookback and append-only callbacks. The #39 fixture grouping and counting the same field and twelve
new empty-child average/percentile eq/neq cases are included. High thresholds 1,025,
1,026 and 10,000 check the verified predecessor-offset boundary/general fallback.
The 49-edge matrix alone does not establish native schemas or nontrivial
multiplicity; complementary stage fixtures supply those checks.

[results.json](results.json) is the unchanged original completed ledger;
[verified-fixtures.json](verified-fixtures.json) adds independent raw hashes and
verdicts. [acceptance-manifest.json](acceptance-manifest.json) is the exact frozen
manifest. [raw-evidence.tar.gz](raw-evidence.tar.gz) preserves every frozen top-level
KQL comparison and raw request/response/engine-version artifact. [raw-files.json](raw-files.json)
and [SHA256SUMS](SHA256SUMS) make original bytes independently checkable.

The earlier254/254 initial revision is preserved separately in
`integrated-acceptance-initial`. Its passing hashes do not substitute for these
refreshed final 255 hashes. The preceding benchmark failures and offset diagnostic
remain unchanged in their own archives. #31/#32 and partial ordered thresholds
remain separate; these fixtures do not establish production capacity.
