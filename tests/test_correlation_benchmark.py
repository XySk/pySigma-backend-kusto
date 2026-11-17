"""Public fixture preparation and strict real-engine benchmark evidence."""

import json
from argparse import Namespace

from tests.fixtures.correlation.benchmark import freeze, run


def test_freeze_pins_public_collection_queries_and_independent_scalar_results(tmp_path):
    freeze(tmp_path, profiles=("sparse",))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["workloads"]) == 16  # seven types, context off/on, two chains
    event = next(item for item in manifest["workloads"] if item["name"] == "sparse-event_count-plain")
    fixture = json.loads((tmp_path / event["fixture"]).read_text())
    assert fixture["expected"] == [
        ["2026-01-01T11:55:00Z", "2026-01-01T12:00:00Z", "g0", 2],
        ["2026-01-01T12:00:00Z", "2026-01-01T12:05:00Z", "g0", 3],
    ]
    assert event["query_bytes"] > 0
    assert "Events" in (tmp_path / event["query"]).read_text()
    assert fixture["schema"]["WindowEnd"] == "datetime"


def test_focused_relation_binding_benchmark_covers_density_reuse_and_context_policies(tmp_path):
    from tests.fixtures.correlation.relation_binding_benchmark import freeze

    freeze(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    names = {item["name"] for item in manifest["workloads"]}

    assert names == {
        "sparse-single-display",
        "dense-unique-single-display",
        "hot-repeated-single-display",
        "dense-multiple-display",
        "dense-boolean-context",
        "hot-ordered-context",
        "dense-child-reuse",
    }
    multiple = (tmp_path / "dense-multiple-display.kql").read_text()
    assert "let CompleteContextRelationships = materialize(" in multiple
    boolean = (tmp_path / "dense-boolean-context.kql").read_text()
    assert "let WindowSourceMatches = materialize(" in boolean

def test_context_transport_scale_benchmark_covers_ticket_53_dimensions(tmp_path):
    from tests.fixtures.correlation.context_transport_benchmark import freeze

    freeze(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    dimensions = {dimension for item in manifest["workloads"] for dimension in item["dimensions"]}

    assert len(manifest["workloads"]) >= 10
    assert dimensions == {
        "density",
        "groups",
        "repetition",
        "selectivity",
        "context-fanout",
        "child-reuse",
        "chain-depth",
        "numeric-pairing",
    }
    assert all(item["transport_model"]["summary_rows"] >= 0 for item in manifest["workloads"])
    assert all(item["query_sha256"] and item["workload_sha256"] for item in manifest["workloads"])

    workloads = {item["name"]: item for item in manifest["workloads"]}
    depth_one = workloads["chain-depth-one"]["transport_model"]
    depth_three = workloads["chain-depth-three"]["transport_model"]
    shared = workloads["shared-child-consumers"]["transport_model"]
    assert len(depth_one["relations"]) == 2
    assert len(depth_three["relations"]) == 4
    assert depth_three["current"]["rows_across_uses"] > depth_one["current"]["rows_across_uses"]
    assert (
        depth_three["candidate"]["estimated_bytes_across_uses"] > depth_one["candidate"]["estimated_bytes_across_uses"]
    )
    assert shared["relations"][0]["parent_consumers"] == 2


def test_benchmark_transport_failure_is_retained_and_never_means_empty_success(tmp_path, monkeypatch):
    import subprocess

    freeze(tmp_path / "frozen", profiles=("sparse",))

    def unavailable(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(subprocess, "run", unavailable)
    args = Namespace(
        manifest=tmp_path / "frozen" / "manifest.json",
        output=tmp_path / "run",
        endpoint="http://example.invalid",
        database="test",
        timeout=1,
        repeats=2,
        only=["sparse-event_count-plain"],
        container=None,
        docker="docker",
        expected_engine=None,
        memory_limit=12884901888,
    )
    assert run(args) == 1
    evidence = json.loads((args.output / "results.json").read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["engine_version"]["status"] == "FAIL"
    assert evidence["attempts"] == []
    assert (args.output / "engine-version.request.json").exists()


def test_runtime_partial_or_cancelled_frames_fail_schema_and_result_gate():
    from tests.fixtures.correlation.benchmark import assess

    empty = {"FrameType": "DataTable", "TableKind": "PrimaryResult", "Rows": []}
    assert assess([empty], {})[0] is False
    completion = {"FrameType": "DataSetCompletion", "HasErrors": False, "Cancelled": True}
    assert assess([empty, completion], {})[0] is False


def test_candidate_context_requires_public_boolean_completeness_even_when_empty():
    from tests.fixtures.correlation.benchmark import assess

    fixture = {"schema": {}, "expected": [], "context": True}
    primary = {"FrameType": "DataTable", "TableKind": "PrimaryResult", "Columns": [], "Rows": []}
    complete = {"FrameType": "DataSetCompletion", "HasErrors": False}
    assert assess([primary, complete], fixture)[0] is False
    assert assess([primary, complete], fixture, baseline=True)[0] is True
    primary["Columns"] = [{"ColumnName": "ContextComplete", "ColumnType": "string"}]
    assert assess([primary, complete], fixture)[0] is False
    primary["Columns"][0]["ColumnType"] = "bool"
    assert assess([primary, complete], fixture)[0] is True


def test_benchmark_compares_each_declared_context_collection_independently():
    from tests.fixtures.correlation.benchmark import assess

    fixture = {
        "context": True,
        "context_fields": ["User", "Amount"],
        "schema": {
            "WindowStart": "datetime",
            "WindowEnd": "datetime",
            "Host": "string",
            "EventCount": "long",
            "User": "dynamic",
            "Amount": "dynamic",
        },
        "expected": [["2026-01-01T11:55:00Z", "2026-01-01T12:00:00Z", "g0", 2, ["a", "b"], [1, 2]]],
    }
    columns = [{"ColumnName": name, "ColumnType": kind} for name, kind in fixture["schema"].items()] + [
        {"ColumnName": "ContextComplete", "ColumnType": "bool"}
    ]
    primary = {
        "FrameType": "DataTable",
        "TableKind": "PrimaryResult",
        "Columns": columns,
        "Rows": [["2026-01-01T11:55:00Z", "2026-01-01T12:00:00Z", "g0", 2, '["b","a"]', "[2,1]", True]],
    }
    complete = {"FrameType": "DataSetCompletion", "HasErrors": False}

    assert assess([primary, complete], fixture)[0] is True


def test_benchmark_preserves_query_resource_measurements_and_exact_output(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    freeze(tmp_path / "frozen", profiles=("sparse",))

    def engine_protocol(command, **kwargs):
        response_file = Path(command[command.index("--output") + 1])
        if command[-1].endswith("/v1/rest/mgmt"):
            payload = {"Tables": [{"Rows": [["protocol-test-engine"]]}]}
        else:
            payload = [
                {
                    "FrameType": "DataTable",
                    "TableKind": "PrimaryResult",
                    "Columns": [
                        {"ColumnName": "WindowStart", "ColumnType": "datetime"},
                        {"ColumnName": "WindowEnd", "ColumnType": "datetime"},
                        {"ColumnName": "Host", "ColumnType": "string"},
                        {"ColumnName": "EventCount", "ColumnType": "long"},
                    ],
                    "Rows": [
                        ["2026-01-01T11:55:00Z", "2026-01-01T12:00:00Z", "g0", 2],
                        ["2026-01-01T12:00:00Z", "2026-01-01T12:05:00Z", "g0", 3],
                    ],
                },
                {
                    "FrameType": "DataTable",
                    "TableKind": "QueryCompletionInformation",
                    "Columns": [{"ColumnName": "EventTypeName"}, {"ColumnName": "Payload"}],
                    "Rows": [
                        [
                            "QueryResourceConsumption",
                            json.dumps(
                                {
                                    "ExecutionTime": 0.5,
                                    "resource_usage": {
                                        "cpu": {"total cpu": "00:00:00.125"},
                                        "memory": {"peak_per_node": 4096},
                                    },
                                }
                            ),
                        ]
                    ],
                },
                {"FrameType": "DataSetCompletion", "HasErrors": False},
            ]
        response_file.write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0, stdout="200", stderr="")

    monkeypatch.setattr(subprocess, "run", engine_protocol)
    args = Namespace(
        manifest=tmp_path / "frozen" / "manifest.json",
        output=tmp_path / "run",
        endpoint="http://example.invalid",
        database="test",
        timeout=15,
        repeats=1,
        only=["sparse-event_count-plain"],
        container=None,
        docker="docker",
        expected_engine="protocol-test-engine",
        memory_limit=12884901888,
    )
    assert run(args) == 0
    attempt = json.loads((args.output / "results.json").read_text())["attempts"][0]
    assert (attempt["status"], attempt["cpu"], attempt["peak_memory"]) == ("PASS", "00:00:00.125", 4096)
    assert attempt["observed_rows"] == 2
