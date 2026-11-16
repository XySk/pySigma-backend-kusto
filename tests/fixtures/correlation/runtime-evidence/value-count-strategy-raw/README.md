# Value-count strategy raw evidence (#39)

This archive preserves the exact earlier candidate-join and active-interval
queries/independent expected-row comparisons, manifests and all twelve paired
responses (two 500-observation profiles, three repeats, two strategies). The
publication rechecks each paired completed response and executed query hash.
It also preserves the initial grouped-context oracle failure and corrected
17-case gate, without changing or upgrading their original ledgers.

The paired runs use the runtime harness: 45-second client timeout and explicit
12,884,901,888-byte per-query memory. Their request options do not explicitly set
server timeout or result-cache age. They are a separate strategy study from the
15/13-second cache-disabled original/final 64 workload benchmarks. The captured
engine version and raw QueryResourceConsumption CPU/planning/execution/memory
frames remain in each response. Container/image continuity was coordinated via
the exclusive lane; this runtime harness did not capture container inspection.

See [the strategy report](../../VALUE-COUNT.md) and
[structured samples](../value-count-strategies.json) for all measurements,
identities and earlier source paths. [raw-files.json](raw-files.json) and
[SHA256SUMS](SHA256SUMS) verify archive contents. These intermediate revisions
are strategy evidence; final integrated results use their own frozen hashes.
