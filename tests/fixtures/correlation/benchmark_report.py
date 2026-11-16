"""Consolidate existing baseline evidence without making engine calls.

Raw ledgers remain unchanged. Only explicitly identified old boxed-sum schema
failures are reassessed, in a separate file, against their original raw frames.
"""

import argparse
import gzip
import io
import json
import statistics
import tarfile
from collections import Counter
from pathlib import Path

from tests.fixtures.correlation.benchmark import BASELINE_REVISION, assess, sha


def seconds(value):
    if value is None:
        return None
    hours, minutes, secs = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def distribution(values):
    values = [value for value in values if value is not None]
    return {"samples": values, "median": statistics.median(values) if values else None}


def resource_stats(payload):
    for frame in payload if isinstance(payload, list) else []:
        if frame.get("TableKind") != "QueryCompletionInformation":
            continue
        columns = [column["ColumnName"] for column in frame.get("Columns", [])]
        for values in frame.get("Rows", []):
            row = dict(zip(columns, values))
            if row.get("EventTypeName") == "QueryResourceConsumption":
                return json.loads(row["Payload"])
    return {}


def consolidate(manifest, evidence, diagnostic, output):
    frozen = json.loads(manifest.read_text())
    assert frozen["revision"] == BASELINE_REVISION
    workloads = {item["name"]: item for item in frozen["workloads"]}
    for item in workloads.values():
        for key in ("query", "fixture"):
            assert sha((manifest.parent / item[key]).read_bytes()) == item[key + "_sha256"]
    output.mkdir(parents=True, exist_ok=False)
    runs, attempts, reassessments = [], [], []
    for directory in evidence:
        ledger = json.loads((directory / "results.json").read_text())
        assert ledger["manifest_sha256"] == sha(manifest.read_bytes()), directory
        assert ledger["timeout_seconds"] == 15
        assert ledger["request_options"] == {
            "max_memory_consumption_per_query_per_node": 12884901888,
            "query_results_cache_max_age": "00:00:00",
            "servertimeout": "00:00:13",
        }
        container = ledger["container"]
        assert container["image"] == "sha256:65e8298d9350856dec696e0e41327288e7bc105146768a1429fce62f3640acd8"
        assert container["memory"] == container["memory_swap"] == 12884901888
        assert "2026.11.15.0741-2645-8e3387b-master" in json.dumps(ledger["engine_version"])
        runs.append(
            {
                "directory": directory.name,
                "started_utc": ledger["started_utc"],
                "recorded_status": ledger["status"],
                "attempts": len(ledger["attempts"]),
                "container": container,
                "engine_version": ledger["engine_version"],
                "request_options": ledger["request_options"],
                "timeout_seconds": ledger["timeout_seconds"],
            }
        )
        for original in ledger["attempts"]:
            item = workloads[original["name"]]
            stem = f'{original["name"]}-{original["repeat"]}'
            request_path, response_path = (
                directory / (stem + suffix) for suffix in (".request.json", ".response.json")
            )
            request = json.loads(request_path.read_text())
            assert sha(request["csl"].encode()) == item["query_sha256"]
            assert original["query_sha256"] == item["query_sha256"]
            assert original["fixture_sha256"] == item["fixture_sha256"]
            row = {
                "run": directory.name,
                "name": item["name"],
                "repeat": original["repeat"],
                "recorded_status": original["status"],
                "detail": original["detail"],
                "elapsed_seconds": original["elapsed_seconds"],
                "verified_status": original["status"],
                "interrupted": original["detail"].startswith("client interrupted"),
                "response": f"{directory.name}/{response_path.name}" if response_path.exists() else None,
            }
            try:
                payload = json.loads(response_path.read_text())
            except (OSError, ValueError):
                payload = None
            if original["detail"].startswith("schema mismatch ValueSum:"):
                fixture = json.loads((manifest.parent / item["fixture"]).read_text())
                passed, detail = assess(payload, fixture, baseline=True)
                primary = next((frame for frame in payload if frame.get("TableKind") == "PrimaryResult"), {})
                reassessments.append(
                    {
                        "run": directory.name,
                        "name": item["name"],
                        "repeat": original["repeat"],
                        "original_status": original["status"],
                        "original_detail": original["detail"],
                        "response_sha256": sha(response_path.read_bytes()),
                        "fixture_sha256": item["fixture_sha256"],
                        "observed_schema": primary.get("Columns"),
                        "reassessed_status": "PASS" if passed else "FAIL",
                        "detail": detail,
                        "reason": "pre-redesign boxed dynamic ValueSum is an explicit schema migration; original ledger unchanged",
                    }
                )
                row["verified_status"] = "PASS" if passed else "FAIL"
                row["reassessment"] = len(reassessments) - 1
            stats = resource_stats(payload)
            usage = stats.get("resource_usage", {})
            cpu = usage.get("cpu", {})
            row.update(
                engine_elapsed_seconds=stats.get("ExecutionTime"),
                cpu_seconds=seconds(cpu.get("total cpu")),
                planning_cpu_seconds=seconds(cpu.get("breakdown", {}).get("query planning")),
                execution_cpu_seconds=seconds(cpu.get("breakdown", {}).get("query execution")),
                peak_memory_bytes=usage.get("memory", {}).get("peak_per_node"),
            )
            attempts.append(row)
    summaries = []
    for name, item in workloads.items():
        rows = [row for row in attempts if row["name"] == name]
        usable = [row for row in rows if not row["interrupted"]]
        assert len(usable) >= 3, (name, len(usable))
        passed = [row for row in usable if row["verified_status"] == "PASS"]
        fixture = json.loads((manifest.parent / item["fixture"]).read_text())
        summaries.append(
            {
                "name": name,
                "input_rows": item["input_rows"],
                "expected_rows": item["expected_rows"],
                "query_sha256": item["query_sha256"],
                "fixture_sha256": item["fixture_sha256"],
                "query_bytes": item["query_bytes"],
                "query_lines": item["query_lines"],
                "operators": item["operators"],
                "expected_schema": fixture["schema"],
                "attempts": len(rows),
                "noninterrupted_attempts": len(usable),
                "verified_passes": len(passed),
                "runtime_failures": len(usable) - len(passed),
                "recorded_failures": sum(row["recorded_status"] == "FAIL" for row in rows),
                "failure_details": dict(Counter(row["detail"] for row in usable if row["verified_status"] != "PASS")),
                "all_client_elapsed_seconds": distribution([row["elapsed_seconds"] for row in usable]),
                "verified_metrics": {
                    key: distribution([row[key] for row in passed])
                    for key in (
                        "elapsed_seconds",
                        "engine_elapsed_seconds",
                        "cpu_seconds",
                        "planning_cpu_seconds",
                        "execution_cpu_seconds",
                        "peak_memory_bytes",
                    )
                },
            }
        )
    archive_index = []
    with (output / "raw-evidence.tar.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for directory in [*evidence, *diagnostic]:
                    for path in sorted(directory.iterdir()):
                        if not path.is_file():
                            continue
                        content = path.read_bytes()
                        name = directory.name + "/" + path.name
                        info = tarfile.TarInfo(name)
                        info.size, info.mode = len(content), 0o644
                        archive.addfile(info, io.BytesIO(content))
                        archive_index.append(
                            {
                                "path": name,
                                "bytes": len(content),
                                "sha256": sha(content),
                                "diagnostic_only": directory in diagnostic,
                            }
                        )

    def write(name, data):
        (output / name).write_text(json.dumps(data, indent=2) + "\n")

    write("raw-files.json", archive_index)
    write("schema-reassessment.json", reassessments)
    write("attempts.json", attempts)
    write(
        "summary.json",
        {
            "baseline_revision": BASELINE_REVISION,
            "manifest_sha256": sha(manifest.read_bytes()),
            "scope": "small synthetic datatables, 4/24/96 input rows; planning and query behavior, not capacity",
            "intermediate_growth": "unobserved; operator text counts are not runtime cardinalities",
            "runs": runs,
            "workloads": summaries,
            "recorded_attempts": len(attempts),
            "recorded_passes": sum(row["recorded_status"] == "PASS" for row in attempts),
            "recorded_failures": sum(row["recorded_status"] == "FAIL" for row in attempts),
            "interrupted_attempts": sum(row["interrupted"] for row in attempts),
            "schema_only_reassessments": len(reassessments),
            "verified_workloads": sum(row["verified_passes"] >= 3 for row in summaries),
        },
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{sha(path.read_bytes())}  {path.name}\n" for path in sorted(output.iterdir()) if path.is_file())
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, nargs="+", required=True)
    parser.add_argument("--diagnostic", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    consolidate(args.manifest, args.evidence, args.diagnostic, args.output)
