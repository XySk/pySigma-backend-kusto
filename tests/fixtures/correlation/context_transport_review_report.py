"""Publish the #53 review-fix transport model and focused correctness evidence."""

import argparse
import json
from pathlib import Path

from tests.fixtures.correlation.benchmark import sha
from tests.fixtures.correlation.context_transport_report import _archive, _checksums


def _verified_run(manifest_path, run_path, expected_status):
    prepared = json.loads(manifest_path.read_text())
    ledger = json.loads((run_path / "results.json").read_text())
    assert ledger["status"] == expected_status
    assert ledger["manifest_sha256"] == sha(manifest_path.read_bytes())
    by_name = {item["name"]: item for item in prepared["fixtures"]}
    for result in ledger["fixtures"]:
        assert result["sha256"] == by_name[result["name"]]["sha256"]
    return prepared, ledger


def publish(
    transport_manifest,
    candidate_manifest,
    candidate_run,
    retained_manifest,
    retained_run,
    failed_runs,
    candidate_source,
    output,
):
    growth = json.loads(transport_manifest.read_text())
    candidate, candidate_result = _verified_run(candidate_manifest, candidate_run, "PASS")
    retained, retained_result = _verified_run(retained_manifest, retained_run, "PASS")
    assert candidate["coverage"] == retained["coverage"]
    assert len(candidate_result["fixtures"]) == len(retained_result["fixtures"]) == 19
    assert all(item["status"] == "PASS" for item in candidate_result["fixtures"] + retained_result["fixtures"])
    failures = []
    for directory in failed_runs:
        ledger = json.loads((directory / "results.json").read_text())
        assert ledger["status"] == "FAIL"
        failure = next(item for item in ledger["fixtures"] if item["status"] == "FAIL")
        failures.append(
            {
                "run": directory.name,
                "manifest_sha256": ledger["manifest_sha256"],
                "fixture": failure["name"],
                "query_sha256": failure["sha256"],
                "detail": failure["detail"],
            }
        )
    workloads = {item["name"]: item["transport_model"] for item in growth["workloads"]}
    depth_one = workloads["chain-depth-one"]
    depth_three = workloads["chain-depth-three"]
    shared = workloads["shared-child-consumers"]
    assert len(depth_one["relations"]) == 2 and len(depth_three["relations"]) == len(shared["relations"]) == 4
    assert depth_three["current"]["rows_across_uses"] > depth_one["current"]["rows_across_uses"]
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "status": "PASS",
        "decision": "retain-production-summary-evidence-union",
        "revision": growth["revision"],
        "transport_manifest_sha256": sha(transport_manifest.read_bytes()),
        "transport_growth": {
            name: {
                "relations": len(model["relations"]),
                "current": model["current"],
                "candidate": model["candidate"],
            }
            for name, model in workloads.items()
        },
        "corrected_candidate": {
            "strategy": candidate["strategy"],
            "manifest_sha256": sha(candidate_manifest.read_bytes()),
            "passed": len(candidate_result["fixtures"]),
            "failed": 0,
        },
        "retained_production": {
            "strategy": retained["strategy"],
            "manifest_sha256": sha(retained_manifest.read_bytes()),
            "passed": len(retained_result["fixtures"]),
            "failed": 0,
        },
        "failed_corrected_candidate_attempts": failures,
        "engine_version": candidate_result["engine_version"],
        "coverage": candidate["coverage"],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "transport-growth-manifest.json").write_bytes(transport_manifest.read_bytes())
    _archive(
        output,
        [
            ("transport-growth", transport_manifest.parent),
            ("corrected-candidate-fixtures", candidate_manifest.parent),
            ("corrected-candidate-run", candidate_run),
            ("retained-production-fixtures", retained_manifest.parent),
            ("retained-production-run", retained_run),
            *((f"failed-{directory.name}", directory) for directory in failed_runs),
            ("corrected-candidate-source", candidate_source),
        ],
    )
    _checksums(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport-manifest", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--candidate-run", type=Path, required=True)
    parser.add_argument("--retained-manifest", type=Path, required=True)
    parser.add_argument("--retained-run", type=Path, required=True)
    parser.add_argument("--failed-run", type=Path, action="append", default=[])
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    publish(
        args.transport_manifest,
        args.candidate_manifest,
        args.candidate_run,
        args.retained_manifest,
        args.retained_run,
        args.failed_run,
        args.candidate_source,
        args.output,
    )
