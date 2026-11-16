"""Bounded context displays and complete relational child evidence."""

import pytest

from sigma.exceptions import SigmaConversionError
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def context_documents():
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    child["fields"] = ["User", "Amount"]
    return [source_document(), child]


def test_default_display_is_bounded_and_reports_completeness_without_assertions():
    (query,) = convert_documents(context_documents())
    assert ", 1000)" in query
    assert "ContextComplete" in query
    assert "assert(" not in query
    assert "1048576" not in query
    assert "make_list(pack_array(pack_array(" not in query


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10", 1048577])
def test_context_limit_requires_an_integer_in_supported_range(limit):
    with pytest.raises(SigmaConversionError, match="correlation_context_limit.*integer.*1.*1048576"):
        convert_documents(context_documents(), backend_options={"correlation_context_limit": limit})


def test_configured_display_limit_is_applied():
    (query,) = convert_documents(context_documents(), backend_options={"correlation_context_limit": 7})
    assert ", 7)" in query
    assert "<= 7" in query


def test_context_complete_collision_requires_alias():
    documents = context_documents()
    documents[-1]["fields"] = ["ContextComplete"]
    with pytest.raises(SigmaConversionError, match="ContextComplete.*explicit correlation alias"):
        convert_documents(documents)


def test_parent_uses_scalar_relationships_without_expanding_public_collections():
    documents = context_documents()
    documents.append(correlation_document("parent", "event_count", ["child"], groups=("User",), condition={"gte": 1}))
    (query,) = convert_documents(documents)
    assert "mv-expand _sigma_child_context_row" not in query
    assert "make_list(pack_array(pack_array(" not in query
    assert "ContextEvidence" in query
    assert "dynamic identity" not in query


def test_requested_grouping_context_stays_scalar_and_complete():
    documents = context_documents()
    documents[-1]["fields"] = ["Host"]
    (query,) = convert_documents(documents)
    assert "ContextComplete = true" in query
    assert "make_list(" not in query


def test_no_requested_context_has_no_display_or_completeness_work():
    documents = context_documents()
    documents[-1].pop("fields")
    (query,) = convert_documents(documents)
    assert "ContextComplete" not in query
    assert "make_list(" not in query


def test_callback_filter_and_metric_update_are_preserved_with_complete_evidence():
    documents = context_documents()
    parent = correlation_document(
        "parent", "value_sum", ["child"], groups=("User",), condition={"field": "EventCount", "gte": 1}
    )
    documents.append(parent)

    def callback(rule, output_format, index, condition, result):
        return (
            result + "\n| where EventCount > 0\n| extend EventCount = EventCount + 10"
            if rule.name == "child"
            else result
        )

    (query,) = convert_documents(documents, callback=callback)
    assert "| where EventCount > 0\n| extend EventCount = EventCount + 10" in query
    assert "EligibleChildMatches" in query
    assert "ContextEvidence" in query


def test_parent_rejects_display_completeness_as_correlation_input():
    documents = context_documents()
    parent = correlation_document(
        "parent",
        "event_count",
        ["child"],
        groups=(),
        condition={"gte": 1},
        aliases={"ChildContextComplete": {"child": "ContextComplete"}},
    )
    parent["fields"] = ["ChildContextComplete"]
    documents.append(parent)
    with pytest.raises(SigmaConversionError, match="ContextComplete is display-only.*parent correlation"):
        convert_documents(documents)


def test_parent_grouping_all_retained_fields_needs_no_child_display_collections():
    documents = context_documents()
    documents.append(
        correlation_document("parent", "event_count", ["child"], groups=("User", "Amount"), condition={"gte": 1})
    )
    (query,) = convert_documents(documents)
    assert "ContextEvidence" in query
    assert "make_list(" not in query
    assert "DistinctValueCount" not in query


def test_focused_relational_runtime_fixtures_match_public_conversion():
    import hashlib
    import json
    from pathlib import Path

    from tests.fixtures.correlation.relational_context import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    frozen = {
        item["name"]: item["sha256"]
        for item in json.loads((directory / "relational-context-manifest.json").read_text())["fixtures"]
    }
    for name, generated in fixtures().items():
        assert (directory / name).read_text() == generated
        assert frozen[name] == hashlib.sha256(generated.encode()).hexdigest()
