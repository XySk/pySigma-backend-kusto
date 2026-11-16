The frozen scalar baseline is backend revision
`07525ee846878754bda7933eab0684895a156086`, before the #34 redesign. Each workload
has its original complete KQL, ordinary Sigma documents, scalar input rows,
independent expected result multiset, required schema, and SHA-256 identities in
`manifest.json`. Generated queries are preserved even when execution fails.
The benchmark does not call private query builders.

The 64 workloads cover all seven correlation types with context off/on, four
input distributions, and mixed direct/child sources through two parent levels.
Sparse data includes simultaneous duplicate contributions, an inclusive five
minute endpoint, and a subsequent observation outside that endpoint. Dense data
uses mostly unique values; many-groups uses twelve small independent groups;
hot-group uses one group, repeated values, timestamp ties and four ordered steps.
Other temporal workloads use three steps. Hot numeric/count thresholds reject
all windows, while dense thresholds qualify many windows. These are deliberately
small deterministic scalar workloads, not capacity tests. No nested dynamic
inputs, overflowing sums, bounded display truncation, or reporting configuration
is part of this comparable baseline.

Expected results come from direct inclusive-window enumeration in `benchmark.py`,
independently of generated KQL. Ordered results require a strict timestamp
increase and collect every participating prefix witness. Nearest rank includes
observation multiplicity. Context is compared as distinct unordered scalar
collections. Full result multiplicity is checked, including overlapping windows.
Mixed/multilevel expectations count a child once at its WindowEnd and preserve
the union of its contributors. Literal sparse count examples also test the
oracle through the public preparation command.

Schema migration is explicit: the old implementation returns boxed dynamic Host
and numeric percentile/sum columns in some paths. The runner accepts those
boxed schemas only for the pinned baseline revision, while candidate runs require
the native scalar schema in each fixture. New `ContextComplete`, when present,
must be true for these below-limit populations. Internal context relationship
columns are not a public schema requirement. All requested result columns are
checked before values are compared.

Run from the relevant checkout with its dependency environment:

```bash
python -m tests.fixtures.correlation.benchmark run \
  --manifest tests/fixtures/correlation/benchmark-baseline/manifest.json \
  --output /tmp/kusto-baseline-new-run \
  --container kusto-spec-17 \
  --docker '/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' \
  --expected-engine 2026.11.15.0741-2645-8e3387b-master \
  --timeout 15 --repeats 3
```

The output directory must be new. The runner records exact engine version,
container image ID and CPU/memory constraints, request settings, manifest/query/
fixture hashes, repeated client elapsed samples, and available engine elapsed,
total CPU, peak-per-node memory, and result-size statistics. Raw management/query
requests, responses and transport outcomes are retained. It saves each attempt
before starting another. Engine result cache is disabled; engine planning and
other warmed state are not reset between repetitions. Keep the engine idle apart
from this runner. Server timeout is two seconds shorter than the HTTP deadline.
The 12 GiB container limit must match the configured query limit. Native image
repository digest can be captured separately with `docker image inspect` when
available; the immutable container image ID is always captured with `--container`.

Failure, timeout, cancellation, HTTP error, missing completion, partial frames,
or nonmatching/schema-invalid output fails the attempt. Empty or unparsable HTTP
responses never count as successful empty output. CPU and peak memory remain
null when the engine does not return them. Intermediate relation growth is
unavailable without a separate per-operator engine profile; input/result row
counts do not substitute for that measurement. The runner continues collecting
attempts after failures without changing resource settings. Retain those failures
in any report; a baseline failure cannot establish a candidate speedup ratio.

For a candidate on the same engine and resource settings:

```bash
python -m tests.fixtures.correlation.benchmark freeze --output /tmp/kusto-candidate
python -m tests.fixtures.correlation.benchmark run \
  --manifest /tmp/kusto-candidate/manifest.json \
  --output /tmp/kusto-candidate-new-run \
  --container kusto-spec-17 \
  --docker '/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' \
  --expected-engine 2026.11.15.0741-2645-8e3387b-master \
  --timeout 15 --repeats 3
```

Freezing refuses to overwrite an existing manifest. Never regenerate this
baseline directory with a candidate backend. Match workloads by name and check
their input documents, rows, expectations and schema; compare each query's byte
and operator counts from the two manifests, and compare distributions of elapsed,
CPU and peak memory only for verified comparable outputs. Operator counts are
textual structure, not runtime operator cardinalities. The runner's own protocol
tests use synthetic frames and are not engine evidence. Earlier focused fixture
results and the runtime gaps documented in `../RUNTIME.md` remain separate from
measurements of this baseline.

Execution evidence for #35 is published in [the baseline report](../BENCHMARK-BASELINE.md).
All 64 workloads have at least three non-interrupted attempts; 28 plain workloads
are verified, while all 36 context/chain workloads failed within the fixed
deadlines. Original success/failure artifacts and explicit schema reassessment
are preserved; preparation alone does not imply runtime correctness.
