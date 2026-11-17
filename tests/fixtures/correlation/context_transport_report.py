"""Verify and publish paired #53 context-transport prototype evidence."""

import argparse
import gzip
import io
import json
import statistics
import tarfile
from pathlib import Path

from tests.fixtures.correlation.benchmark import sha
from tests.fixtures.correlation.benchmark_report import seconds


def _load_manifest(path):
    data = json.loads(path.read_text())
    workloads = {item["name"]: item for item in data["workloads"]}
    assert len(workloads) == len(data["workloads"])
    for item in workloads.values():
        for key in ("query", "fixture"):
            assert sha((path.parent / item[key]).read_bytes()) == item[f"{key}_sha256"]
    return data, workloads


def _load_run(path, manifest, workloads, minimum_repeats=5):
    data = json.loads((path / "results.json").read_text())
    assert data["status"] == "PASS"
    assert data["manifest_sha256"] == sha(manifest.read_bytes())
    assert data["repeats"] >= minimum_repeats
    assert all(item["status"] == "PASS" for item in data["attempts"])
    for attempt in data["attempts"]:
        workload = workloads[attempt["name"]]
        assert attempt["query_sha256"] == workload["query_sha256"]
        assert attempt["fixture_sha256"] == workload["fixture_sha256"]
    return data


def _median(rows, field):
    values = [seconds(row[field]) if field == "cpu" else row[field] for row in rows]
    assert all(value is not None for value in values)
    return statistics.median(values)


def _archive(output, sources, stem="raw-evidence"):
    index = []
    with (output / f"{stem}.tar.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for label, directory in sources:
                    for path in sorted(directory.iterdir()):
                        if not path.is_file():
                            continue
                        content = path.read_bytes()
                        name = f"{label}/{path.name}"
                        info = tarfile.TarInfo(name)
                        info.size, info.mode = len(content), 0o644
                        archive.addfile(info, io.BytesIO(content))
                        index.append({"path": name, "bytes": len(content), "sha256": sha(content)})
    (output / f"{stem.removesuffix('-evidence')}-files.json").write_text(json.dumps(index, indent=2) + "\n")


def _checksums(output):
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha(path.read_bytes())}  {path.name}\n"
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "SHA256SUMS"
        )
    )


def publish(baseline_manifest, baseline_run, candidate_manifest, candidate_run, failed, output):
    baseline_data, baseline = _load_manifest(baseline_manifest)
    candidate_data, candidate = _load_manifest(candidate_manifest)
    assert baseline.keys() == candidate.keys()
    for name in baseline:
        assert (baseline_manifest.parent / baseline[name]["fixture"]).read_bytes() == (
            candidate_manifest.parent / candidate[name]["fixture"]
        ).read_bytes()
    old = _load_run(baseline_run, baseline_manifest, baseline)
    new = _load_run(candidate_run, candidate_manifest, candidate)
    workloads = []
    regressions = []
    target_improvements = []
    for name in baseline:
        before = [row for row in old["attempts"] if row["name"] == name]
        after = [row for row in new["attempts"] if row["name"] == name]
        assert len(before) >= 5 and len(after) >= 5
        before_cpu, after_cpu = _median(before, "cpu"), _median(after, "cpu")
        before_memory, after_memory = _median(before, "peak_memory"), _median(after, "peak_memory")
        cpu_change = after_cpu / before_cpu - 1
        memory_change = after_memory / before_memory - 1
        if cpu_change <= -0.15 or memory_change <= -0.15:
            target_improvements.append(name)
        if cpu_change > 0.10 or memory_change > 0.10:
            regressions.append(name)
        workloads.append(
            {
                "name": name,
                "dimensions": candidate[name]["dimensions"],
                "workload_sha256": candidate[name]["workload_sha256"],
                "baseline_query_sha256": baseline[name]["query_sha256"],
                "candidate_query_sha256": candidate[name]["query_sha256"],
                "query_bytes": {"baseline": baseline[name]["query_bytes"], "candidate": candidate[name]["query_bytes"]},
                "operators": {"baseline": baseline[name]["operators"], "candidate": candidate[name]["operators"]},
                "transport_model": candidate[name]["transport_model"],
                "samples": {"baseline": len(before), "candidate": len(after)},
                "median_cpu_seconds": {"baseline": before_cpu, "candidate": after_cpu},
                "cpu_change": cpu_change,
                "median_peak_memory_bytes": {"baseline": before_memory, "candidate": after_memory},
                "peak_memory_change": memory_change,
                "median_elapsed_seconds": {
                    "baseline": _median(before, "elapsed_seconds"),
                    "candidate": _median(after, "elapsed_seconds"),
                },
                "median_engine_elapsed_seconds": {
                    "baseline": _median(before, "engine_elapsed_seconds"),
                    "candidate": _median(after, "engine_elapsed_seconds"),
                },
                "failures": {"baseline": 0, "candidate": 0},
                "operator_cardinality": "unavailable from response frames",
            }
        )
    failed_prototypes = []
    for directory in failed:
        ledger = json.loads((directory / "results.json").read_text())
        requests = []
        for request_path in sorted(directory.glob("*.request.json")):
            if request_path.name == "engine-version.request.json":
                continue
            request = json.loads(request_path.read_text())
            requests.append({"name": request_path.stem, "query_sha256": sha(request["csl"].encode())})
        failed_prototypes.append(
            {
                "directory": directory.name,
                "status": ledger["status"],
                "manifest_sha256": ledger.get("manifest_sha256"),
                "failure": ledger.get("error")
                or next((item["detail"] for item in ledger.get("fixtures", []) if item["status"] == "FAIL"), None),
                "queries": requests,
                "workloads": [
                    {"name": item["name"], "workload_sha256": item["sha256"]} for item in ledger.get("fixtures", [])
                ],
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "status": "PASS",
        "decision": "reject-separate-transport",
        "adoption_gate": {
            "required_target_cpu_or_memory_improvement": 0.15,
            "maximum_representative_regression": 0.10,
            "target_improvements": target_improvements,
            "representative_regressions": regressions,
            "adopted": bool(target_improvements) and not regressions,
        },
        "baseline_revision": baseline_data["revision"],
        "candidate_revision": candidate_data["revision"],
        "baseline_manifest_sha256": sha(baseline_manifest.read_bytes()),
        "candidate_manifest_sha256": sha(candidate_manifest.read_bytes()),
        "baseline_samples": len(old["attempts"]),
        "candidate_samples": len(new["attempts"]),
        "failed_prototypes": failed_prototypes,
        "engine_version": new["engine_version"],
        "container": new.get("container"),
        "request_options": new["request_options"],
        "workloads": workloads,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    _archive(
        output,
        [
            ("baseline-fixtures", baseline_manifest.parent),
            ("candidate-fixtures", candidate_manifest.parent),
            ("baseline-run", baseline_run),
            ("candidate-run", candidate_run),
            *((f"failed-{directory.name}", directory) for directory in failed),
        ],
    )
    _checksums(output)
    return summary


def publish_final_gates(acceptance_manifest, acceptance_run, broad_manifest, broad_run, output):
    acceptance = json.loads((acceptance_run / "results.json").read_text())
    assert acceptance["status"] == "PASS" and len(acceptance["fixtures"]) == 255
    assert acceptance["manifest_sha256"] == sha(acceptance_manifest.read_bytes())
    _, broad_workloads = _load_manifest(broad_manifest)
    broad = _load_run(broad_run, broad_manifest, broad_workloads, minimum_repeats=1)
    assert len(broad_workloads) == 64 and len(broad["attempts"]) == 64
    summary = {
        "status": "PASS",
        "acceptance_manifest_sha256": sha(acceptance_manifest.read_bytes()),
        "strict_fixtures": {"passed": 255, "failed": 0},
        "broad_manifest_sha256": sha(broad_manifest.read_bytes()),
        "broad_workloads": {"passed": 64, "failed": 0},
        "engine_version": broad["engine_version"],
        "container": broad.get("container"),
        "request_options": broad["request_options"],
    }
    (output / "final-gates.json").write_text(json.dumps(summary, indent=2) + "\n")
    _archive(
        output,
        [
            ("acceptance-fixtures", acceptance_manifest.parent),
            ("acceptance-run", acceptance_run),
            ("broad-fixtures", broad_manifest.parent),
            ("broad-run", broad_run),
        ],
        "final-gates-raw-evidence",
    )
    _checksums(output)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--candidate-run", type=Path, required=True)
    parser.add_argument("--failed", type=Path, nargs="*", default=[])
    parser.add_argument("--acceptance-manifest", type=Path)
    parser.add_argument("--acceptance-run", type=Path)
    parser.add_argument("--broad-manifest", type=Path)
    parser.add_argument("--broad-run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = publish(
        args.baseline_manifest,
        args.baseline_run,
        args.candidate_manifest,
        args.candidate_run,
        args.failed,
        args.output,
    )
    final = (args.acceptance_manifest, args.acceptance_run, args.broad_manifest, args.broad_run)
    if any(final):
        assert all(final), "all final-gate paths are required together"
        publish_final_gates(*final, args.output)
    print(json.dumps({key: result[key] for key in ("status", "decision", "baseline_samples", "candidate_samples")}))
