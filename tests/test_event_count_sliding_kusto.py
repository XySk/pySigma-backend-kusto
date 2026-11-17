"""Exact event-count queries through ordinary Sigma collection conversion."""

import pytest

from tests.correlation_helpers import convert_documents, correlation_document, source_document


@pytest.mark.parametrize("groups", [(), ("Host",)])
@pytest.mark.parametrize(
    "operator,symbol", [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")]
)
def test_actual_event_count_uses_weighted_boundaries_for_general_comparisons(groups, operator, symbol):
    source = source_document()
    source["detection"]["alternate"] = {"Marker": "match"}
    source["detection"]["condition"] = ["selection", "alternate"]
    count = correlation_document("count", "event_count", ["source-rule"], groups=groups, condition={operator: 2.5})

    (query,) = convert_documents([source, count])

    # The readable public query must avoid enumerating every event/window pair.
    assert "row_cumsum(" in query
    assert "join kind=inner" not in query
    assert "mv-expand" not in query
    assert query.count('Marker =~ "match"') == 2
    assert "SourceMatchCount = count()" in query
    assert f"| where todecimal(EventCount) {symbol} decimal(2.5)" in query
    assert "WindowStart = " in query and "WindowEnd = " in query
    assert "assert(" not in query and "_sigma_identity" not in query


def test_weighted_count_names_do_not_shadow_source_fields_or_tables():
    from tests.correlation_helpers import fixture_schema

    source = source_document()
    source["fields"] = [
        "EventTime",
        "SourceMatchCount",
        "CountBoundaryTime",
        "CountBoundaryOrder",
        "SourceMatchDelta",
        "GroupHost",
    ]
    count = correlation_document("count", "event_count", ["source-rule"], condition={"gte": 1})
    schema = fixture_schema(
        ("SourceMatchArrivals", "NetworkEvents"),
        SourceMatchCount="long",
        CountBoundaryTime="datetime",
        CountBoundaryOrder="long",
        SourceMatchDelta="long",
        GroupHost="string",
    )

    (query,) = convert_documents([source, count], tables=("SourceMatchArrivals", "NetworkEvents"), schema=schema)

    assert 'SourceMatchArrivals\n| where Marker =~ "match"' in query
    assert "let SourceMatchArrivals_1 = " in query
    assert 'EventTime_1 = ["Timestamp"]' in query
    assert 'GroupHost_1 = ["Host"]' in query
    assert "SourceMatchCount_1 = count()" in query
    assert "EventCount = row_cumsum(SourceMatchDelta_1" in query
    assert '= ["SourceMatchCount"]' in query


def test_weighted_count_fixtures_match_current_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.event_count_sliding import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query
