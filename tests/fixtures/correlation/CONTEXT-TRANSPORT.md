# Context transport scale decision (#53)

Child correlations continue to expose public summaries and complete relationship
evidence through the existing two-branch transport union.  A prototype that
exposed those relations separately was correct after one failed query-shape
iteration, but it was rejected by the paired performance gate.

## Prototype

The candidate replaced `ContextRowKind` selection with separately bound summary
and evidence queries.  Because independently evaluated `row_number()` values are
not stable, it also derived deterministic match IDs from each summary's window
and grouping identity.  Parents that needed structural empty-context matches
left-joined the summary relation to complete evidence; evidence-only consumers
selected only the complete relation.

The first candidate incorrectly retained `project-away` for evidence columns on
the now-separate summary relation.  Those columns existed only because Kusto's
old union supplied a common schema.  Kusto rejected the query with `SEM0104`.
Removing that operation restored callback, empty-evidence, shared, multilevel,
999/1000/1001, and relationship correctness.

## Scale matrix

`context_transport_benchmark.py` freezes 15 public-conversion workloads with an
independent inclusive-window and relationship oracle.  It varies increasing
input/window density, one hot group versus many groups, repeated versus unique
values, low/high qualifying selectivity, one/three retained fields and
relationship fan-out, shared child consumers, chain depth one/three, and
repeated/unique numeric-context pairing.

The manifest records input/output sizes, exact workload/query hashes, query
bytes, textual operator counts, and modeled summary/evidence transport rows and
bytes. The transport model enumerates every context-bearing relation and every
parent/public use in the workload. Its totals therefore grow with shared-child
fan-out and chain depth; they are not the former child-only estimate that made
depth one, depth three, and shared graphs appear identical. Current-union bytes
include repeated summary columns on evidence rows; candidate evidence bytes use
only stable match identity plus the relationship. The runner records CPU, peak
memory, elapsed time, failures, engine and resource settings. Per-operator
runtime cardinality is explicitly unavailable from the captured Kusto frames
and is not approximated from operator text.

## Decision

Five paired samples per workload all passed: 75/75 baseline and 75/75 candidate.
Separate transport lowered median CPU at least 15% in seven workloads, including
92.2% for depth three and 70.4% for shared consumers.  It also raised median CPU
35.9–48.5% for dense/hot 256-row workloads and raised representative peak memory
65–152% in most workloads.  These confirmed regressions exceed the 10% guard.

The separate transport is therefore rejected.  Production retains the #52
union, shared materialization, and stable ID assignment.  Exact positive and
negative evidence is published in
[`runtime-evidence/context-transport-final`](runtime-evidence/context-transport-final/README.md).
