"""Numeric retained values use the public collection conversion seam."""

import pytest

from tests.correlation_helpers import convert_documents, numeric_documents


@pytest.mark.parametrize("kind", ["value_sum", "value_avg", "value_percentile"])
@pytest.mark.parametrize("groups", [(), ("User",)])
def test_numeric_parent_consumes_retained_values_at_child_window_end(kind, groups):
    (query,) = convert_documents(numeric_documents(kind, groups))
    assert "| mv-expand _sigma_child_context_row" in query
    assert 'todatetime(["WindowEnd"])' in query
    assert {"value_sum": "ValueSum", "value_avg": "ValueAvg", "value_percentile": "ValuePercentile"}[kind] in query


@pytest.mark.parametrize("generate", [False, True])
@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte", "eq", "neq"])
def test_numeric_context_preserves_comparisons_and_generation_controls(generate, operator):
    docs = numeric_documents()
    docs[1]["correlation"]["generate"] = generate
    docs[-1]["correlation"]["condition"] = {"field": "Amount", operator: 15.5}
    queries = convert_documents(docs)
    assert len(queries) == (2 if generate else 1)
    assert "15.5" in queries[-1]


def test_numeric_parent_rejects_unavailable_child_field_with_schema():
    from sigma.exceptions import SigmaConversionError

    docs = numeric_documents(field="Missing")
    with pytest.raises(SigmaConversionError, match="does not return required field 'Missing'"):
        convert_documents(docs)


def test_measured_alias_resolves_logical_child_field():
    docs = numeric_documents(field="Measurement")
    docs[-1]["correlation"]["aliases"] = {"Measurement": {"child": "Amount"}}
    (query,) = convert_documents(docs)
    assert "ValueSum" in query
    assert "| mv-expand _sigma_child_context_row" in query


@pytest.mark.parametrize("kind", ["value_sum", "value_count", "value_avg", "value_percentile"])
@pytest.mark.parametrize("groups", [(), ("Host",), ("Amount",)])
def test_empty_child_context_preserves_metric_anchors_only_without_retained_grouping(kind, groups):
    from tests.fixtures.correlation.empty_child_context import documents

    (query,) = convert_documents(documents(kind, groups))
    placeholder = 'iff(array_length(["_sigma_context_rows"]) == 0, dynamic([null]), ["_sigma_context_rows"])'
    assert (placeholder in query) == (groups != ("Amount",))


def test_empty_child_context_runtime_fixtures_use_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.empty_child_context import fixtures

    for name, query in fixtures().items():
        assert Path(__file__).with_name("fixtures").joinpath("correlation", name).read_text() == query
