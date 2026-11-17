"""Exact numeric percentile through standard Sigma collection conversion."""

import pytest

from sigma.exceptions import SigmaConversionError
from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document


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
    assert "todecimal(ValuePercentile) >= decimal(2.5)" in query
    assert 'MeasuredAmount = ["Amount"]' in query
    assert "pack_array(gettype(" not in query
    assert "percentiles(" not in query
    assert "make_list(" not in query
    assert '["Host"] = GroupHost, ValuePercentile' in query


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
    if selector in (0, 100):
        assert f"ValuePercentile = {'min' if selector == 0 else 'max'}(MeasuredAmount)" in query
        assert "| sort by" not in query
    else:
        assert f"(PercentilePopulation / 100) * {selector}" in query
    assert "isnotnull(MeasuredAmount)" in query


def test_percentile_scalar_metric_chaining_uses_child_window_end():
    parent = correlation_document(
        "parent",
        "value_percentile",
        ["percentile"],
        condition={"field": "ValuePercentile", "percentile": 100, "gte": 0},
    )
    (query,) = convert_documents([parent, percentile(), source_document()])
    assert 'EventTime = ["WindowEnd"]' in query
    assert 'MeasuredValuePercentile = ["ValuePercentile"]' in query


@pytest.mark.parametrize("kind", ["int", "long", "real", "decimal"])
def test_percentile_orders_declared_native_values_and_preserves_metric(kind):
    (query,) = convert_documents([source_document(), percentile()], schema=fixture_schema(Amount=kind))
    assert "MeasuredAmount asc" in query
    assert "ValuePercentile = MeasuredAmount" in query
    assert "pack_array(gettype(" not in query
    assert "_sigma_numeric" not in query
    if kind == "real":
        assert "isfinite(MeasuredAmount)" in query
    if kind == "decimal":
        assert "ValuePercentile >= decimal(2.5)" in query


def test_native_percentile_runtime_fixtures_use_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.native_percentile import fixtures

    for name, text in fixtures().items():
        assert Path(__file__).with_name("fixtures").joinpath("correlation", name).read_text() == text
