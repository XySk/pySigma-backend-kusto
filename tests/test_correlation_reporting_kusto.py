"""Reporting options at the ordinary Sigma collection conversion boundary."""

import pytest

from sigma.exceptions import SigmaConversionError
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def documents():
    return [source_document(), correlation_document("count", "event_count", ["source-rule"])]


def test_explicit_report_bounds_are_readable_and_filter_public_window_ends():
    query = convert_documents(
        documents(),
        backend_options={
            "correlation_report_start": "2026-01-01T10:00:00Z",
            "correlation_report_end": "2026-01-01T11:00:00Z",
        },
    )[0]
    assert "let ReportStart = datetime(2026-01-01T10:00:00.0000000Z);" in query
    assert "let ReportEnd = datetime(2026-01-01T11:00:00.0000000Z);" in query
    assert query.endswith("| where WindowEnd >= ReportStart and WindowEnd < ReportEnd")
    assert '["Timestamp"] >= ReportStart - 300s' in query
    assert '["Timestamp"] < ReportEnd' in query


@pytest.mark.parametrize(
    "options, message",
    [
        ({"correlation_report_start": "yesterday"}, "correlation_report_start"),
        ({"correlation_report_end": 123}, "correlation_report_end"),
        ({"correlation_report_start": "2026-02-30T10:00:00Z"}, "ISO 8601"),
        ({"correlation_report_start": "2026-01-01T10:00:00"}, "timezone"),
        ({"correlation_report_start": "2026-01-01T10:00:00.12345678Z"}, "seven fractional"),
        (
            {
                "correlation_report_start": "2026-01-01T10:00:00.0000002Z",
                "correlation_report_end": "2026-01-01T10:00:00.0000001Z",
            },
            "earlier",
        ),
        (
            {"correlation_report_start": "2026-01-01T10:00:00Z", "correlation_report_end": "2026-01-01T11:00:00+01:00"},
            "earlier",
        ),
    ],
)
def test_invalid_report_configuration_is_actionable(options, message):
    with pytest.raises(SigmaConversionError, match=message):
        convert_documents(documents(), backend_options=options)


def test_reporting_preserves_seventh_fractional_digit_and_normalizes_timezone():
    query = convert_documents(
        documents(), backend_options={"correlation_report_start": "2026-01-01T11:00:00.1234567+01:00"}
    )[0]
    assert "datetime(2026-01-01T10:00:00.1234567Z)" in query
    assert "ReportEnd" not in query


def test_omitted_bounds_preserve_caller_source_history():
    query = convert_documents(documents())[0]
    assert "ReportStart" not in query and "ReportEnd" not in query and "ago(" not in query


def test_report_names_avoid_source_columns_even_when_the_columns_are_not_requested():
    from tests.correlation_helpers import fixture_schema

    query = convert_documents(
        documents(),
        schema=fixture_schema(ReportStart="datetime", ReportEnd="datetime"),
        backend_options={
            "correlation_report_start": "2026-01-01T10:00:00Z",
            "correlation_report_end": "2026-01-01T11:00:00Z",
        },
    )[0]
    assert "let ReportStart_ =" in query and "let ReportEnd_ =" in query
    assert '["Timestamp"] >= ReportStart_ - 300s' in query


def test_shared_multilevel_children_use_maximum_accumulated_lookback_in_any_document_order():
    source = source_document()
    child = correlation_document("child", "event_count", ["source-rule"], span="3m")
    near = correlation_document("near", "event_count", ["child"], span="2m")
    far = correlation_document("far", "event_count", ["child"], span="7m")
    root = correlation_document("root", "event_count", ["far"], span="11m")
    for inputs in ([source, child, near, far, root], [root, far, near, child, source]):
        queries = convert_documents(inputs, backend_options={"correlation_report_start": "2026-01-01T10:00:00Z"})
        assert len(queries) == 2
        assert all('["Timestamp"] >= ReportStart - 1260s' in query for query in queries)
        # Internal child window ends remain unrestricted; only each public root owns its cutoff.
        assert all(query.count("| where WindowEnd >=") == 1 for query in queries)


def test_standalone_child_has_public_cutoff_and_parent_keeps_complete_history_and_callback():
    source = source_document()
    child = correlation_document("child", "event_count", ["source-rule"], span="3m")
    parent = correlation_document("parent", "event_count", ["child"], span="7m", generate=True)

    def callback(rule, output, index, condition, query):
        return query + "\n| where EventCount >= 2" if rule.name == "child" else query

    queries = convert_documents(
        [parent, child, source], callback=callback, backend_options={"correlation_report_start": "2026-01-01T10:00:00Z"}
    )
    assert len(queries) == 2
    assert all('["Timestamp"] >= ReportStart - 600s' in query for query in queries)
    assert queries[0].count("| where WindowEnd >=") == 1
    assert queries[1].count("| where WindowEnd >=") == 1
    assert all("| where EventCount >= 2" in query for query in queries)


def test_context_children_keep_complete_internal_history_when_reporting_and_callbacks_are_combined():
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    child["fields"] = ["User"]
    parent = correlation_document("parent", "value_count", ["child"], field="User", generate=True)

    def callback(rule, output, index, condition, query):
        return query + "\n| where EventCount >= 2" if rule.name == "child" else query

    queries = convert_documents(
        [source_document(), child, parent],
        callback=callback,
        backend_options={"correlation_report_start": "2026-01-01T10:00:00Z", "correlation_context_limit": 1},
    )
    assert len(queries) == 2
    assert all(query.count("| where WindowEnd >=") == 1 for query in queries)
    assert "| where EventCount >= 2" in queries[1]
    assert "ContextEvidence" in queries[1]


def test_end_only_interval_and_backend_reuse_do_not_leak_previous_history_plan():
    import yaml

    from sigma.backends.kusto import KustoBackend
    from sigma.collection import SigmaCollection
    from sigma.processing.pipeline import ProcessingPipeline
    from tests.correlation_helpers import fixture_schema

    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    pipeline.vars["kusto_schema"] = fixture_schema()
    backend = KustoBackend(
        processing_pipeline=pipeline,
        correlation_timestamp_field="Timestamp",
        correlation_report_end="2026-01-01T11:00:00Z",
    )
    (first,) = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all(documents())))
    assert "let ReportStart" not in first
    assert '["Timestamp"] < ReportEnd' in first
    assert first.endswith("| where WindowEnd < ReportEnd")
    backend.backend_options.pop("correlation_report_end")
    (second,) = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all(documents())))
    assert "ReportStart" not in second and "ReportEnd" not in second


def test_reporting_engine_fixtures_match_current_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.reporting import fixtures

    for name, query in fixtures().items():
        assert Path(__file__).with_name("fixtures").joinpath("correlation", name).read_text() == query
