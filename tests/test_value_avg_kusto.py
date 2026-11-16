"""Public collection conversion contracts for numeric averages."""

import pytest

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.test_correlation_kusto import final_summary_projection


@pytest.mark.parametrize(
    ("operator", "symbol"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_average_preserves_fractional_comparisons_and_excludes_undefined_windows(operator, symbol):
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("average", "value_avg", ["source-rule"], condition={"field": "Amount", operator: 2.5}),
        ]
    )

    assert '_sigma_value = pack_array(gettype(["Amount"]), ["Amount"])' in query
    assert "ValueAvg = avgif(" in query
    assert f"| where _sigma_numeric_count > 0 and ValueAvg {symbol} 2.5" in query
    assert final_summary_projection(query).endswith('["Host"] = _sigma_group_0, ValueAvg')


@pytest.mark.parametrize(
    ("kind", "metric", "condition"),
    [
        ("event_count", "EventCount", {"gte": 1}),
        ("value_count", "ValueCount", {"field": "User", "gte": 1}),
        ("value_sum", "ValueSum", {"field": "Amount", "gte": 0}),
        ("value_avg", "ValueAvg", {"field": "Amount", "gte": 0}),
        ("temporal", "RuleCount", {"gte": 1}),
        ("temporal_ordered", "RuleCount", {"gte": 1}),
    ],
)
def test_average_consumes_available_scalar_child_metrics_once_at_window_end(kind, metric, condition):
    child = correlation_document("child", kind, ["source-rule"], condition=condition)
    parent = correlation_document("parent", "value_avg", ["child"], condition={"field": metric, "gte": 0})
    (query,) = convert_documents([parent, source_document(), child])

    assert '_sigma_timestamp = todatetime(["WindowEnd"])' in query
    assert f'pack_array(gettype(["{metric}"]), ["{metric}"])' in query
    assert final_summary_projection(query).endswith('["Host"] = _sigma_group_0, ValueAvg')


@pytest.mark.parametrize("child_generate", [False, True])
@pytest.mark.parametrize("parent_generate", [False, True])
def test_average_exposes_metric_to_chained_consumers_and_preserves_generate(child_generate, parent_generate):
    child = correlation_document(
        "child", "value_avg", ["source-rule"], generate=child_generate, condition={"field": "Amount", "gte": 0}
    )
    parent = correlation_document(
        "parent", "value_sum", ["child"], generate=parent_generate, condition={"field": "ValueAvg", "gte": 0}
    )
    queries = convert_documents([source_document(), child, parent])

    assert len(queries) == 1 + child_generate + parent_generate
    assert (
        any(final_summary_projection(query).endswith('["Host"] = _sigma_group_0, ValueAvg') for query in queries)
        == parent_generate
    )
    assert final_summary_projection(queries[-1]).endswith('["Host"] = _sigma_group_0, ValueSum')
    assert any('pack_array(gettype(["ValueAvg"]), ["ValueAvg"])' in query for query in queries)


def test_average_honors_builtin_source_mapping():
    from sigma.backends.kusto import KustoBackend
    from sigma.pipelines.microsoft365defender import microsoft_365_defender_pipeline
    from tests.test_correlation_kusto import correlation_collection

    collection = correlation_collection(
        """correlation:
  type: value_avg
  rules:
    - source-rule
  timespan: 5m
  condition:
    field: ProcessId
    gt: 0.5
"""
    )
    (query,) = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    ).convert(collection)

    assert "DeviceProcessEvents" in query
    assert 'pack_array(gettype(["ProcessId"]), ["ProcessId"])' in query
    assert "ValueAvg > 0.5" in query


@pytest.mark.parametrize(
    ("kind", "condition"),
    [
        ("event_count", {"gte": 1}),
        ("value_count", {"field": "ValueAvg", "gte": 1}),
        ("value_sum", {"field": "ValueAvg", "gte": 0}),
        ("value_avg", {"field": "ValueAvg", "gte": 0}),
        ("temporal", {"gte": 1}),
        ("temporal_ordered", {"gte": 1}),
    ],
)
def test_average_child_composes_with_every_supported_consumer(kind, condition):
    child = correlation_document("child", "value_avg", ["source-rule"], condition={"field": "Amount", "gte": 0})
    parent = correlation_document("parent", kind, ["child"], field=condition.get("field"), condition=condition)

    (query,) = convert_documents([source_document(), child, parent])

    assert "ValueAvg = avgif(" in query
    assert 'todatetime(["WindowEnd"])' in query
