"""Public conversion checks for typed group compatibility."""

from pathlib import Path

from tests.fixtures.correlation.typed_grouping import fixtures


def test_direct_grouping_returns_values_without_string_coercion():
    from tests.correlation_helpers import convert_documents, correlation_document, source_document

    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("typed", "event_count", ["source-rule"], groups=("User",), condition={"gte": 1}),
        ]
    )
    assert ' = tostring(["User"])' not in query


def test_typed_grouping_fixtures_are_current():
    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query


def test_grouped_retained_context_fixtures_are_current():
    from tests.fixtures.correlation.typed_grouped_context import fixtures as context_fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in context_fixtures().items():
        assert (directory / name).read_text() == query
