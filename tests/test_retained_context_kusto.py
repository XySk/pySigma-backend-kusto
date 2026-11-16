"""Retained investigation fields through the standard public conversion seam."""

import pytest

from sigma.exceptions import SigmaConversionError
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_count_retains_requested_source_and_correlation_fields_without_regrouping():
    source = source_document()
    source["fields"] = ["User", "Host", "User"]
    correlation = correlation_document("context", "event_count", ["source-rule"])
    correlation["fields"] = ["Server", "User"]

    (query,) = convert_documents([source, correlation])

    assert '["User"] = ' in query
    assert '["Server"] = ' in query
    assert '["Host"] = GroupHost' in query
    assert '["ContextComplete"]' in query
    assert "assert(" not in query


def test_retained_context_omits_null_values_from_displayed_collection_and_limit_count():
    source = source_document()
    source["fields"] = ["Amount"]
    summary = correlation_document("context", "event_count", ["source-rule"])

    (query,) = convert_documents([source, summary])

    assert "| where isnotnull(ContextAmount)" in query
    assert "make_list(ContextAmount, 1000)" in query
    assert "DistinctAmountCount = count()" in query


@pytest.mark.parametrize(
    "kind,metric",
    [
        ("event_count", "EventCount"),
        ("value_count", "ValueCount"),
        ("value_sum", "ValueSum"),
        ("value_avg", "ValueAvg"),
        ("value_percentile", "ValuePercentile"),
        ("temporal", "RuleCount"),
    ],
)
def test_supported_summaries_expose_context_without_replacing_metric(kind, metric):
    condition = {"gte": 1}
    if kind.startswith("value_"):
        condition["field"] = "Amount"
    if kind == "value_percentile":
        condition["percentile"] = 50
    summary = correlation_document("summary", kind, ["source-rule"], field="Amount", condition=condition)
    summary["fields"] = ["User"]
    (query,) = convert_documents([source_document(), summary])
    assert f'["{metric}"]' in query
    assert '["User"] = ' in query


def test_retained_result_name_collision_explains_explicit_alias():
    summary = correlation_document("summary", "event_count", ["source-rule"])
    summary["fields"] = ["WindowEnd"]
    with pytest.raises(SigmaConversionError, match="OriginalWindowEnd.*aliases:"):
        convert_documents([source_document(), summary])


def test_source_context_collision_can_be_resolved_by_explicit_alias():
    source = source_document()
    source["fields"] = ["WindowEnd"]
    summary = correlation_document(
        "summary",
        "event_count",
        ["source-rule"],
        aliases={
            "OriginalWindowEnd": {"source-rule": "WindowEnd"},
        },
    )
    summary["fields"] = ["OriginalWindowEnd"]
    (query,) = convert_documents([source, summary])
    assert '["OriginalWindowEnd"] = ' in query
    assert '= ["WindowEnd"]' in query


def test_ordered_summaries_retain_context_from_successful_full_sequences():
    first = source_document("a", "a")
    first["fields"] = ["User"]
    last = source_document("b", "b")
    summary = correlation_document("ordered", "temporal_ordered", ["a", "b"])
    summary["fields"] = ["Server"]
    (query,) = convert_documents([first, last, summary])
    assert '["User"] = ' in query
    assert '["Server"] = ' in query
    assert '["RuleCount"]' in query


def test_ordered_child_exposes_retained_fields_to_parent_conversion():
    first, last = source_document("a", "a"), source_document("b", "b")
    child = correlation_document("ordered", "temporal_ordered", ["a", "b"])
    child["fields"] = ["User", "Server"]
    parent = correlation_document("parent", "value_count", ["ordered"], field="User")
    (query,) = convert_documents([first, last, child, parent])
    assert '["ValueCount"]' in query
    assert "ContextEvidence" in query
    assert "mv-expand _sigma_child_context_row" not in query
