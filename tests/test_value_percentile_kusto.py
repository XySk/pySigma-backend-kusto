"""Exact numeric percentile through standard Sigma collection conversion."""

import pytest

from sigma.exceptions import SigmaConversionError
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def percentile(condition=None, **kwargs):
    return correlation_document(
        "percentile",
        "value_percentile",
        ["source-rule"],
        condition=condition or {"field": "Amount", "percentile": 50, "gte": 2.5},
        **kwargs,
    )


def test_percentile_selects_exact_observation_and_preserves_fractional_threshold():
    (query,) = convert_documents([source_document(), percentile()])

    assert "row_number(" in query
    assert "toreal(ValuePercentile) >= 2.5" in query
    assert 'pack_array(gettype(["Amount"]), ["Amount"])' in query
    assert "percentiles(" not in query
    assert "make_list(" not in query
    assert query.endswith('["Host"] = _sigma_group_0, ValuePercentile')


@pytest.mark.parametrize("selector", [-1, 101, 49.9, 100.9, -0.5, True, "50"])
def test_percentile_rejects_invalid_selectors_even_after_parser_coercion(selector):
    with pytest.raises(SigmaConversionError, match="percentile selector must be an integer from 0 through 100"):
        convert_documents([source_document(), percentile({"field": "Amount", "percentile": selector, "neq": 0})])


@pytest.mark.parametrize("selector", [0, 50, 100])
@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte", "eq", "neq"])
def test_percentile_accepts_endpoint_selectors_and_six_comparisons(selector, operator):
    (query,) = convert_documents(
        [source_document(), percentile({"field": "Amount", "percentile": selector, operator: 2.5})]
    )
    assert f"(_sigma_population / 100) * {selector}" in query
    assert "isfinite(toreal(" in query


def test_percentile_scalar_metric_chaining_uses_child_window_end():
    parent = correlation_document(
        "parent",
        "value_percentile",
        ["percentile"],
        condition={"field": "ValuePercentile", "percentile": 100, "gte": 0},
    )
    (query,) = convert_documents([parent, percentile(), source_document()])
    assert '_sigma_timestamp = todatetime(["WindowEnd"])' in query
    assert 'pack_array(gettype(["ValuePercentile"]), ["ValuePercentile"])' in query
