"""Source shaping through ordinary Sigma collection conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document


def test_single_native_datetime_source_is_bare_and_filters_canonical_rows_in_its_branch():
    source = source_document()
    count = correlation_document("count", "event_count", ["source-rule"])

    (query,) = convert_documents([source, count])

    assert "let SourceMatchArrivals = Events\n" in query
    assert "let SourceMatchArrivals = union " not in query
    assert (
        'EventTime_1 = ["Timestamp"], GroupHost = ["Host"]\n| where isnotnull(EventTime_1) and isnotempty(GroupHost)'
        in query
    )
    assert 'todatetime(["Timestamp"])' not in query


def test_multiple_converted_searches_from_one_rule_are_combined_with_union():
    source = source_document()
    source["detection"]["alternate"] = {"Marker": "other"}
    source["detection"]["condition"] = ["selection", "alternate"]
    count = correlation_document("count", "event_count", ["source-rule"])

    (query,) = convert_documents([source, count])

    assert "let SourceMatchArrivals = union (Events\n" in query
    assert query.count("| project EventTime_1 =") == 2
    assert query.count("| where isnotnull(EventTime_1) and isnotempty(GroupHost)") == 2


def test_multiple_referenced_rules_are_combined_with_union():
    first = source_document("first", "one")
    second = source_document("second", "two")
    count = correlation_document("count", "event_count", ["first", "second"])

    (query,) = convert_documents([first, second, count])

    assert "let SourceMatchArrivals = union (Events\n" in query
    assert query.count("| where isnotnull(EventTime_1) and isnotempty(GroupHost)") == 2


def test_child_window_end_uses_its_native_datetime_type_without_conversion():
    source = source_document()
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    parent = correlation_document("parent", "event_count", ["child"], condition={"gte": 1})

    (query,) = convert_documents([source, child, parent])

    assert 'EventTime = ["WindowEnd"]' in query
    assert 'todatetime(["WindowEnd"])' not in query


def test_string_timestamp_keeps_conversion_report_lookback_and_branch_validation():
    source = source_document()
    count = correlation_document("count", "event_count", ["source-rule"])

    (query,) = convert_documents(
        [source, count],
        schema=fixture_schema(Timestamp="string"),
        backend_options={"correlation_report_start": "2026-01-01T10:00:00Z"},
    )

    assert 'todatetime(["Timestamp"]) >= ReportStart - 300s' in query
    assert 'EventTime_1 = todatetime(["Timestamp"])' in query
    assert "| where isnotnull(EventTime_1) and isnotempty(GroupHost)" in query


def test_shared_branch_validation_keeps_empty_counted_values_as_anchors():
    source = source_document()
    count = correlation_document("count", "value_count", ["source-rule"], field="User")

    (query,) = convert_documents([source, count])

    branch = query.split("let ValueCountSources = materialize(", 1)[1].split(");", 1)[0]
    assert "| where isnotnull(EventTime_1) and isnotempty(GroupHost)" in branch
    assert "isnotempty(CountedUser)" not in branch


def test_context_reconstruction_filters_invalid_canonical_rows_inside_each_source_branch():
    source = source_document()
    count = correlation_document("count", "event_count", ["source-rule"], condition={"gte": 1})
    count["fields"] = ["User"]

    (query,) = convert_documents([source, count])

    assert 'ContextEventTime = ["Timestamp"], ContextGroupHost = ["Host"], ContextUser = ["User"]' in query
    assert "| where isnotnull(ContextEventTime) and isnotempty(ContextGroupHost)" in query
