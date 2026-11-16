# Verified predecessor-offset boundary and preserved failure

The first integrated candidate completed 189 of 192 benchmark samples. Three
`hot-group-mixed-multilevel-plain` samples failed with HTTP 400; their original
non-JSON `General_BadRequest` bodies, requests, ledgers and frozen queries are
preserved in [the initial candidate archive](../integrated-candidate-initial/raw-evidence.tar.gz).
That query emitted `prev(EventTime, 9999)` for an internal threshold 10,000.
Those failed samples are never treated as empty results or acceptance passes.

On engine `2026.11.15.0741-2645-8e3387b-master`, minimized probes completed for
offsets 1, 100, 1000 and 1024. Offset 1025 failed with HTTP 400 and the detailed
`RelopSemanticException` response: `SEM0248: prev() function expects an integer
in range [1..1024] for argument 2`. This is observed engine-version evidence;
the public prev documentation describes the integer offset without publishing
this upper bound. The probe run stops at that failure; prepared offsets 4096
and 9999 were not executed by that minimized run.

The production planner now specializes only positive integer thresholds at most
1,025 (offset N-1); larger lower bounds use general exact event counts. Public
conversion regressions check the boundary, `gt`, threshold 10,000 and a value
beyond signed-int range. The positive engine fixture compares thresholds 1,025,
1,026 and 10,000 against literal expected results for exactly-N and N-1 tied
failure batches followed by success. Both optimized/fallback and callback-forced
general paths pass. The retained response/request identifies its exact header
hash; the final integrated replay separately certifies the refreshed current file.

[raw-evidence.tar.gz](raw-evidence.tar.gz) preserves the original minimized
queries/manifest, partial failing probe ledger and raw responses, plus the
successful positive-threshold fixture execution (15-second client deadline,
12 GiB query memory). [raw-files.json](raw-files.json) and
[SHA256SUMS](SHA256SUMS) make all original bytes checkable. The failed probe is
intentional diagnostic evidence, not a supported-input acceptance case.
