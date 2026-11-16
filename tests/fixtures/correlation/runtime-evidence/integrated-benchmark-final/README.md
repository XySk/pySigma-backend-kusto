# Final integrated 64-workload evidence

All 192 samples pass independently rechecked successful uncancelled completion,
native schemas and exact independent expected multisets. The measured production
revision is `59c455b5a5b008c042727eaef06f92c0da832044`; all 64 input/oracle/schema
JSONs are byte-identical to original baseline fixtures. Settings, methodology,
per-workload tables, planning/execution/memory exceptions and all preserved
failures are documented in [BENCHMARK-COMPARISON.md](../../BENCHMARK-COMPARISON.md).

[summary.json](summary.json) carries all 64 original/final query byte/line/operator
maps, samples, independent metrics and ratios only for verified originals.
[attempts.json](attempts.json) retains every final raw-response reassessment.
[raw-evidence.tar.gz](raw-evidence.tar.gz) contains the exact frozen manifest,
queries/oracles and all original final-run requests/responses/transport records,
engine version, container inspection and original ledger. [raw-files.json](raw-files.json)
checks every original archive byte, and [SHA256SUMS](SHA256SUMS) checks publication
files. The initial three HTTP 400 failures remain in a separate archive, unchanged.

`benchmark_comparison.py` reproduces these structured files offline from the
archived frozen/run directories without engine calls. It validates image/version,
12 GiB query/container memory/swap and unchanged CPU cap, exact 15/13-second/cache-zero
request settings, and every fixture/query/request hash before assessing results.
