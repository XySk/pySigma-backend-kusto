# Reporting bounds evidence (#45)

The initial strict runtime gate passed all eight fixtures at implementation
`b8c276c`, before merging #42/#47 integration `40bc40c`. Frozen query identities,
full requests, raw responses, successful uncancelled completion frames and engine
version are archived in `initial-b8c276c/`. The engine is
`2026.11.15.0741-2645-8e3387b-master`; the per-query memory limit was 12 GiB and
client timeout 45 seconds. This gate establishes correctness, not a production
capacity or performance promise.

Independent literal result multisets cover the ten failures spanning
10:00–10:09 followed by success10:18 (both predecessor and full-count child
plans), adjacent half-open reports separated by 100 ns ticks, inclusive
correlation endpoints, shared children with unequal multilevel timespans and
reversed document order, source-column/scalar-name collisions, and existing
string timestamp conversion including malformed values.

The retained-context fixture retains prereport children and complete typed
relationships through three levels despite display limit1. A child callback adds
10 to EventCount and selects exactly EventCount12, so only the10:01 child feeds
the success10:08; distinct ancestor values are exactly a,b,s, excluding c.
Standalone child output is empty while middle output reports incomplete bounded
display and the final parent still counts all three retained values.

Integration merge `915ed23` preserves #47 structural Summary skeletons and real
context-evidence markers plus #42 native sums. Only the context/callback query
changes on regeneration; the other seven queries remain byte-identical to this
successful initial gate. The refreshed context query passed its separate strict runtime gate in
`integrated-915ed23/`, including an exact one-row/window/metric check for the
standalone middle output. Its manifest entry matches the current generated
fixture. Original evidence remains intact. All eight current reporting fixture
hashes therefore have successful strict engine evidence.
