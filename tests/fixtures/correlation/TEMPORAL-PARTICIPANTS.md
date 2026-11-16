# Temporal participants

Ordered retained context uses reachability thresholds within each successful correlation match's inclusive window. A candidate at position i participates when it is strictly later than the earliest reachable prefix endpoint at i-1 and strictly earlier than the latest reachable suffix start at i+1. The final position must occur at the actual completion timestamp. Missing threshold rows are unreachable, enforced by inner joins.

The earliest reachable prefix endpoint gives the least restrictive valid predecessor. The latest reachable suffix start gives the least restrictive valid successor. Their witnesses concatenate around the candidate because the timestamps are strictly separated. All candidates already share one completion's `[WindowEnd - timespan, WindowEnd]`, so concatenation respects the total span. Conversely, every full sequence supplies both witnesses. Repeated references are separate positions; equal timestamps cannot advance adjacent positions.

Each threshold relation contains at most one row per correlation match. Membership joins candidates only to these scalar threshold relations, then unions participant relationship IDs and selects the complete source rows. No stage joins populations of alternative paths. Complete context remains a native relationship relation consumed by parents; display truncation and `ContextComplete` remain separate.

Boolean evidence keeps the existing parsed-expression recurrence: AND gates all child evidence by the whole conjunction; OR unions evidence from each satisfied child; negation propagates to leaves with De Morgan's laws, and absence leaves supply no values. The correctness gate includes all satisfied alternatives, incomplete alternatives, precedence, nested negation, absence-only evidence, shared sources and multilevel parents.

Names expose source and field roles (`ContextUser`, `ContextGroupHost`, `CandidatesForFailure`, `EarliestFailurePrefix`, `LatestRetrySuffix`, `OrderedParticipants`). Allocation reserves existing query identifiers and logical fields; punctuation becomes underscores and collisions gain suffixes. Public logical field names are quoted and preserved.

`temporal_participants.py` prepares public-conversion fixtures and freezes sequence-length/branching workloads. Its correctness oracle exhaustively enumerates strictly ordered tuples and takes their contributor union; it does not use the backend's reachability recurrence. Benchmark stage diagnostics independently count window/source pairs, enumeration paths, scalar thresholds and selected participants. These extra diagnostics are measured separately from timed full-result workloads.

The remaining expensive stage pairs successful completions with source rows in their windows. Dense overlapping completions can therefore still grow with completion/source pairs. The threshold algorithm removes path products, not that pairing or the display collection cost. Small deterministic datatables establish neither production capacity nor a universal latency guarantee.

## Successful-completion simplifications

Each qualifying summary proves that a full sequence exists. Every final source row at that completion can replace its final witness; the preceding suffix bound is the completion itself. This removes the final prefix and suffix threshold relations and their joins. One/two-step sequences need only source identity and strict endpoint filters. Source relationship IDs are allocated only when the longer-sequence membership join consumes them.

An evidence-free child is still a source match. Context-bearing child inputs therefore retain a structural Summary row with a nullable `HasContextEvidence` marker. Only actual Evidence rows set the marker true. Ordered/Boolean membership sees the structural row, while complete retained tuples and displays filter it out. This prevents missing sibling evidence and prevents native string-null defaults from becoming fabricated empty-string context. Both ordered and Boolean regressions fail before this correction and pass afterward; failure responses are preserved.

## Engine verification and measurements

Implementation `a1bc0af` includes integration `a134c76` (tickets 39/48). Final 33/33 independently expected correctness diagnostics and 4/4 intermediate-count diagnostics pass. The correctness set covers one-step and repeated five/seven-position rules, strict ties and total-span boundaries, overlapping completions, grouped/ungrouped aliases, native NaN/infinities/signed zero, all satisfied Boolean alternatives, precedence/nested negation, empty-evidence children, complete parent context, shared/mixed/multilevel relationships, and downstream dedup/metrics. Raw responses and frozen execution hashes are archived in [runtime-evidence/temporal-participants](runtime-evidence/temporal-participants).

The initial full-threshold reachability strategy passed but had nearly flat/slightly worse elapsed medians; its samples are preserved in [benchmarks.json](runtime-evidence/temporal-participants/benchmarks.json). Successful-endpoint simplification reduced planning stages. The final candidate has 24/24 passing timed samples (eight context off/on workloads, three repetitions); unchanged enumeration has another 24/24. Every fixture hash is identical across versions; no-context query hashes are unchanged and their timing variations are controls, not implementation speedups.

Same Kusto engine `2026.11.15.0741-2645-8e3387b-master`, same container image, 12 GiB memory/swap, result cache off, 15-second client/13-second server timeouts. Tables show median engine elapsed seconds, total CPU seconds, and peak bytes across three samples. Enumeration is revision `75c3da7`; final reachability is `a1bc0af`.

| Sequence / branching / context | Elapsed before → after | CPU before → after | Peak bytes before → after |
| --- | ---: | ---: | ---: |
| 3 / 3 / on | 0.265964 → 0.171988 | 0.247904 → 0.161608 | 12,169,536 → 11,122,032 |
| 5 / 3 / on | 1.065959 → 0.756063 | 1.013838 → 0.717133 | 15,336,672 → 14,855,648 |
| 7 / 3 / on | 3.117185 → 2.451050 | 3.007824 → 2.337841 | 24,318,880 → 18,015,680 |
| 5 / 6 / on | 1.213958 → 0.772594 | 1.156007 → 0.737555 | 35,909,568 → 14,876,944 |

Actual engine stage diagnostics confirm 24/42/60/165 paired source rows and 27/243/2,187/7,776 full enumeration tuples for these workloads. Final reachability's scalar threshold relations contain only three or six rows each (one per completion), and selected relationship populations are 21/39/57/150. Final context query bytes are 5,295/8,686/12,261/8,686 versus enumeration 5,819/8,815/12,083/8,815. Longer readable queries can still remove product populations; byte count alone is not a performance claim.

The measured improvements include planning/compilation and execution; small deterministic datatables are not production capacity evidence. Window/source pairing, scalar joins for longer sequences, and per-field display collections remain. Larger workloads and the final all-type integrated gate remain parent-spec work. Frozen raw samples live under `/tmp/ticket47-runtime`; prior failed absence-child diagnostics and the sandbox-only transport failure are recorded separately rather than overwritten.
