"""Public conversion checks for typed group compatibility."""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("groups", [("Host", "Region"), ("Host", "Region", "User", "Server")])
def test_group_tuple_restoration_uses_one_lookup_per_result(groups):
    """Avoid repeating the full source/identity plan for each grouping field."""
    from tests.correlation_helpers import convert_documents, correlation_document, source_document

    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("typed", "event_count", ["source-rule"], groups=groups, condition={"gte": 1}),
        ]
    )
    restores = [line for line in query.splitlines() if "| join kind=inner (_sigma_group_lookup | project" in line]
    assert len(restores) == 1
    for restore in restores:
        assert restore.count("_sigma_group_original") == len(groups)
        assert restore.endswith('on ["Host"]')


def test_typed_grouping_fixtures_are_current():
    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query


def test_grouped_retained_context_fixtures_are_current():
    from tests.fixtures.correlation.typed_grouped_context import fixtures as context_fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in context_fixtures().items():
        assert (directory / name).read_text() == query
