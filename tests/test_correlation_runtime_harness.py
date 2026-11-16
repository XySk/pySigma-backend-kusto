"""Protocol acceptance of the reusable v2 runtime gate, without engine calls."""

import pytest

from tests.fixtures.correlation.runtime import assess_response, read_primary_result


def response(rows=None, errors=False):
    return [
        {"FrameType": "DataTable", "TableKind": "PrimaryResult", "Rows": rows or []},
        {"FrameType": "DataSetCompletion", "HasErrors": errors},
    ]


def test_clean_empty_multiset_diagnostics_pass():
    assert read_primary_result(response()) == []
    assert assess_response(response()) == (True, "empty multiset diagnostic")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        response([["extra", 1]]),
        response(errors=True),
        response()[:1],
        response()[1:],
        response() + response(),
        [{"FrameType": "DataTable", "TableKind": "PrimaryResult"}, response()[1]],
        response() + [{"FrameType": "Error", "Message": "broken"}],
    ],
)
def test_missing_frames_duplicates_errors_and_nonempty_diagnostics_fail(payload):
    assert assess_response(payload)[0] is False


def test_overflow_requires_explicit_engine_error_and_expected_message():
    error = {"error": {"code": "SEM0080", "message": "Kusto value_sum integer overflow"}}
    assert assess_response(error, expected_error="Kusto value_sum integer overflow")[0]
    assert not assess_response(response(), expected_error="Kusto value_sum integer overflow")[0]
    assert not assess_response(
        {"error": {"message": "other failure"}}, expected_error="Kusto value_sum integer overflow"
    )[0]
    # A string in successful primary rows is not an engine error.
    assert not assess_response(
        response([["Kusto value_sum integer overflow"]]), expected_error="Kusto value_sum integer overflow"
    )[0]


def test_transport_timeout_stops_before_second_fixture_and_preserves_failure(tmp_path, monkeypatch):
    import json
    import subprocess
    from argparse import Namespace
    from types import SimpleNamespace

    from tests.fixtures.correlation.runtime import manifest, run

    (tmp_path / "one.kql").write_text("print diagnostic=1")
    (tmp_path / "two.kql").write_text("print diagnostic=2")
    manifest_path = tmp_path / "acceptance-manifest.json"
    manifest_path.write_text(json.dumps(manifest(tmp_path)))
    output = tmp_path / "evidence"
    query_calls = []

    def curl(command, **kwargs):
        from pathlib import Path

        response_file = Path(command[command.index("--output") + 1])
        if command[-1].endswith("/v1/rest/mgmt"):
            response_file.write_text(json.dumps({"Tables": [{"Rows": [["test engine version"]]}]}))
            return SimpleNamespace(returncode=0, stdout="200", stderr="")
        query_calls.append(response_file.name)
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(subprocess, "run", curl)
    args = Namespace(
        manifest=manifest_path, output=output, endpoint="http://example.invalid", database="test", timeout=60, only=None
    )
    assert run(args) == 1
    assert query_calls == ["one.response.json"]
    record = json.loads((output / "results.json").read_text())
    assert record["status"] == record["fixtures"][0]["status"] == "FAIL"
    assert len(record["fixtures"]) == 1
    assert record["engine_version"]["Tables"][0]["Rows"] == [["test engine version"]]
    assert record["fixtures"][0]["sha256"]
    assert record["request_options"]["max_memory_consumption_per_query_per_node"] == 12884901888


def test_cancelled_empty_result_is_not_success():
    payload = response()
    payload[-1]["Cancelled"] = True
    assert assess_response(payload)[0] is False
    with pytest.raises(ValueError, match="cancelled"):
        read_primary_result(payload)


def test_each_verified_fixture_is_durable_before_next_request(tmp_path, monkeypatch):
    import json
    import subprocess
    from argparse import Namespace
    from pathlib import Path
    from types import SimpleNamespace

    from tests.fixtures.correlation.runtime import manifest, run

    for name in ("first.kql", "second.kql"):
        (tmp_path / name).write_text("print diagnostic=1")
    manifest_path = tmp_path / "acceptance-manifest.json"
    manifest_path.write_text(json.dumps(manifest(tmp_path)))
    output = tmp_path / "evidence"
    query_calls = 0

    def curl(command, **kwargs):
        nonlocal query_calls
        response_file = Path(command[command.index("--output") + 1])
        if command[-1].endswith("/v1/rest/mgmt"):
            payload = {"Tables": [{"Rows": [["test engine version"]]}]}
        else:
            query_calls += 1
            if query_calls == 2:
                record = json.loads((output / "results.json").read_text())
                assert record["status"] == "RUNNING"
                assert [(item["name"], item["status"]) for item in record["fixtures"]] == [("first.kql", "PASS")]
                assert not (output / "results.json.tmp").exists()
                raise KeyboardInterrupt
            payload = response()
        response_file.write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0, stdout="200", stderr="")

    monkeypatch.setattr(subprocess, "run", curl)
    args = Namespace(
        manifest=manifest_path, output=output, endpoint="http://example.invalid", database="test", timeout=60, only=None
    )
    with pytest.raises(KeyboardInterrupt):
        run(args)
    assert json.loads((output / "results.json").read_text())["fixtures"][0]["status"] == "PASS"
