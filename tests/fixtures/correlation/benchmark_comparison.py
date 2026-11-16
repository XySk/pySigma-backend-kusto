"""Offline, hash-checked publication of the final 64-workload comparison.

No engine calls. Reassess candidate raw frames against the frozen independent
oracles; preserve all ledger verdicts and failures. Never derive a speedup from a
baseline workload that did not complete correctly.
"""

import argparse
import gzip
import io
import json
import tarfile
from collections import Counter
from pathlib import Path

from tests.fixtures.correlation.benchmark import assess, sha
from tests.fixtures.correlation.benchmark_report import distribution, resource_stats, seconds

HERE = Path(__file__).parent
ENGINE = "2026.11.15.0741-2645-8e3387b-master"
IMAGE = "sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8"
MEMORY = 12884901888
METRICS = (
    "elapsed_seconds",
    "engine_elapsed_seconds",
    "cpu_seconds",
    "planning_cpu_seconds",
    "execution_cpu_seconds",
    "peak_memory_bytes",
)


def frozen_workloads(manifest):
    frozen = json.loads(manifest.read_text())
    workloads = {item["name"]: item for item in frozen["workloads"]}
    assert len(workloads) == len(frozen["workloads"]), "duplicate frozen workload"
    for item in workloads.values():
        for key in ("query", "fixture"):
            assert sha((manifest.parent / item[key]).read_bytes()) == item[key + "_sha256"], item["name"]
    return frozen, workloads


def verify_runs(manifest, directories, timeout=15):
    frozen, workloads = frozen_workloads(manifest)
    attempts, runs = [], []
    options = {
        "max_memory_consumption_per_query_per_node": MEMORY,
        "query_results_cache_max_age": "00:00:00",
        "servertimeout": f"00:00:{timeout - 2:02d}",
    }
    for directory in directories:
        ledger = json.loads((directory / "results.json").read_text())
        assert ledger["status"] in ("PASS", "FAIL"), "unfinished ledger is not acceptance"
        assert ledger["manifest_sha256"] == sha(manifest.read_bytes()), directory
        assert ledger["revision"] == frozen["revision"], directory
        assert ledger["timeout_seconds"] == timeout and ledger["request_options"] == options, directory
        container = ledger["container"]
        assert container["image"] == IMAGE and container["memory"] == container["memory_swap"] == MEMORY, directory
        assert container["cpu_quota"] == container["nano_cpus"] == 0, directory
        assert ledger["engine_version"]["status"] == "PASS" and ENGINE in json.dumps(
            ledger["engine_version"]
        ), directory
        runs.append(
            {
                "directory": directory.name,
                "ledger_sha256": sha((directory / "results.json").read_bytes()),
                **{
                    key: ledger[key]
                    for key in (
                        "revision",
                        "started_utc",
                        "status",
                        "container",
                        "engine_version",
                        "request_options",
                        "timeout_seconds",
                    )
                },
            }
        )
        for original in ledger["attempts"]:
            item = workloads[original["name"]]
            stem = f'{original["name"]}-{original["repeat"]}'
            request_path = directory / (stem + ".request.json")
            response_path = directory / (stem + ".response.json")
            request = json.loads(request_path.read_text())
            assert sha(request["csl"].encode()) == item["query_sha256"] == original["query_sha256"], stem
            assert original["fixture_sha256"] == item["fixture_sha256"], stem
            assert json.loads(request["properties"])["Options"] == options, stem
            fixture = json.loads((manifest.parent / item["fixture"]).read_text())
            try:
                payload = json.loads(response_path.read_text())
                verified, detail = assess(payload, fixture)
            except (OSError, ValueError, TypeError, KeyError, StopIteration) as error:
                payload, verified, detail = None, False, str(error)
            # A failed transport/ledger cannot be upgraded by later raw parsing.
            passed = original["status"] == "PASS" and verified
            stats = resource_stats(payload)
            usage = stats.get("resource_usage", {})
            cpu = usage.get("cpu", {})
            attempts.append(
                {
                    "run": directory.name,
                    "name": item["name"],
                    "repeat": original["repeat"],
                    "recorded_status": original["status"],
                    "recorded_detail": original["detail"],
                    "verified_status": "PASS" if passed else "FAIL",
                    "verification_detail": detail,
                    "query_sha256": item["query_sha256"],
                    "fixture_sha256": item["fixture_sha256"],
                    "response": f"{directory.name}/{response_path.name}" if response_path.exists() else None,
                    "response_sha256": sha(response_path.read_bytes()) if response_path.exists() else None,
                    "elapsed_seconds": original["elapsed_seconds"],
                    "engine_elapsed_seconds": stats.get("ExecutionTime"),
                    "cpu_seconds": seconds(cpu.get("total cpu")),
                    "planning_cpu_seconds": seconds(cpu.get("breakdown", {}).get("query planning")),
                    "execution_cpu_seconds": seconds(cpu.get("breakdown", {}).get("query execution")),
                    "peak_memory_bytes": usage.get("memory", {}).get("peak_per_node"),
                }
            )
    return runs, attempts


def archive_evidence(output, sources):
    index = []
    with (output / "raw-evidence.tar.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for label, directory in sources:
                    for path in sorted(directory.iterdir()):
                        if not path.is_file():
                            continue
                        content = path.read_bytes()
                        name = label + "/" + path.name
                        info = tarfile.TarInfo(name)
                        info.size, info.mode = len(content), 0o644
                        archive.addfile(info, io.BytesIO(content))
                        index.append({"path": name, "bytes": len(content), "sha256": sha(content)})
    (output / "raw-files.json").write_text(json.dumps(index, indent=2) + "\n")


def write_checksums(output):
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha(path.read_bytes())}  {path.name}\n"
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "SHA256SUMS"
        )
    )


def compare(manifest, evidence, output):
    baseline_manifest = HERE / "benchmark-baseline" / "manifest.json"
    baseline_summary = HERE / "runtime-evidence" / "benchmark-baseline" / "summary.json"
    frozen, candidate = frozen_workloads(manifest)
    baseline_frozen, baseline = frozen_workloads(baseline_manifest)
    summaries = {item["name"]: item for item in json.loads(baseline_summary.read_text())["workloads"]}
    assert len(candidate) == 64 and candidate.keys() == baseline.keys() == summaries.keys()
    for name, item in candidate.items():
        old = baseline[name]
        # These exact JSONs carry inputs, ordinary Sigma documents, native schema,
        # and independent expected values. Only generated KQL is replaced.
        assert (manifest.parent / item["fixture"]).read_bytes() == (
            baseline_manifest.parent / old["fixture"]
        ).read_bytes(), name
        assert (
            summaries[name]["query_sha256"] == old["query_sha256"]
            and summaries[name]["fixture_sha256"] == old["fixture_sha256"]
        ), name
    runs, attempts = verify_runs(manifest, evidence)
    workloads = []
    for name, item in candidate.items():
        rows = [row for row in attempts if row["name"] == name]
        passed = [row for row in rows if row["verified_status"] == "PASS"]
        old = summaries[name]
        metrics = {key: distribution([row[key] for row in passed]) for key in METRICS}
        baseline_passed = old["verified_passes"] >= 3 and old["runtime_failures"] == 0
        candidate_passed = len(passed) >= 3 and len(passed) == len(rows)
        workloads.append(
            {
                "name": name,
                "input_rows": item["input_rows"],
                "expected_rows": item["expected_rows"],
                "fixture_sha256": item["fixture_sha256"],
                "baseline": old,
                "candidate": {
                    **{key: item[key] for key in ("query_sha256", "query_bytes", "query_lines", "operators")},
                    "attempts": len(rows),
                    "verified_passes": len(passed),
                    "runtime_failures": len(rows) - len(passed),
                    "failure_details": dict(
                        Counter(row["recorded_detail"] for row in rows if row["verified_status"] != "PASS")
                    ),
                    "verified_metrics": metrics,
                },
                "candidate_accepted": candidate_passed,
                "client_elapsed_ratio_baseline_over_candidate": (
                    old["verified_metrics"]["elapsed_seconds"]["median"] / metrics["elapsed_seconds"]["median"]
                    if baseline_passed and candidate_passed
                    else None
                ),
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "status": "PASS" if all(item["candidate_accepted"] for item in workloads) else "FAIL",
        "candidate_revision": frozen["revision"],
        "candidate_manifest_sha256": sha(manifest.read_bytes()),
        "baseline_revision": baseline_frozen["revision"],
        "baseline_manifest_sha256": sha(baseline_manifest.read_bytes()),
        "baseline_summary_sha256": sha(baseline_summary.read_bytes()),
        "fixture_identity": "all64 input/oracle/schema JSONs byte-identical",
        "scope": "4/24/96-row datatables; query/planning behavior, not production throughput/capacity",
        "intermediate_growth": "unobserved; textual operator counts are not runtime cardinalities",
        "runs": runs,
        "recorded_attempts": len(attempts),
        "verified_samples": sum(row["verified_status"] == "PASS" for row in attempts),
        "failed_samples": sum(row["verified_status"] != "PASS" for row in attempts),
        "accepted_workloads": sum(row["candidate_accepted"] for row in workloads),
        "workloads": workloads,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "attempts.json").write_text(json.dumps(attempts, indent=2) + "\n")
    archive_evidence(
        output, [("frozen-candidate", manifest.parent), *((directory.name, directory) for directory in evidence)]
    )
    write_checksums(output)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.manifest, args.evidence, args.output)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "recorded_attempts", "verified_samples", "failed_samples", "accepted_workloads")
            }
        )
    )
    raise SystemExit(result["status"] != "PASS")
