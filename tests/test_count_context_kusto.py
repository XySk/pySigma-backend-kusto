"""Count parents consume observed retained relationships through public conversion."""

import pytest

from sigma.exceptions import SigmaConversionError

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def documents(kind="event_count", groups=("User", "Server"), **kwargs):
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    child["fields"] = ["User", "Server"]
    parent = correlation_document("parent", kind, ["child"], groups=groups, condition={"gte": 1}, **kwargs)
    return [source_document(), child, parent]


@pytest.mark.parametrize("kind", ["event_count", "value_count"])
def test_count_parent_converts_observed_child_context_without_counting_display_lists(kind):
    (query,) = convert_documents(documents(kind))
    assert "| mv-expand " in query
    assert "Kusto dynamic identity traversal incomplete" in query
    assert 'todatetime(["WindowEnd"])' in query
    assert ("EventCount" if kind == "event_count" else "ValueCount") in query
    assert "| mv-expand _sigma_child_context_row" in query


def test_logical_aliases_for_retained_fields_use_child_returned_schema():
    docs = documents(groups=("Actor",), aliases={"Actor": {"child": "User"}})
    (query,) = convert_documents(docs)
    assert '["Actor"]' in query


def test_missing_retained_field_reports_actual_child_schema():
    docs = documents(groups=("Missing",))
    with pytest.raises(SigmaConversionError, match="does not return required field 'Missing'.*Available fields"):
        convert_documents(docs)


@pytest.mark.parametrize("generate", [False, True])
def test_retained_context_parent_preserves_standalone_child_generation(generate):
    docs = documents()
    docs[1]["correlation"]["generate"] = generate
    queries = convert_documents(docs)
    assert len(queries) == (2 if generate else 1)
    assert "| mv-expand _sigma_child_context_row" in queries[-1]
    if generate:
        assert "| mv-expand _sigma_child_context_row" not in queries[0]


def test_multiple_context_children_and_direct_sources_convert_together():
    docs = documents(groups=("User",))
    other = correlation_document("other-child", "event_count", ["other-source"], condition={"gte": 1})
    other["fields"] = ["User", "Server"]
    docs[-1]["correlation"]["rules"] = ["child", "other-child", "direct"]
    docs[2:2] = [source_document("other-source", "other"), other, source_document("direct", "direct")]
    (query,) = convert_documents(docs)
    assert '"other-child"' in query
    assert '"direct"' in query


def test_child_context_alias_can_reference_uuid_when_parent_uses_logical_name():
    child_id = "efbfa9b1-4d01-41a0-9596-1b212730afba"
    docs = documents(groups=("Actor",), aliases={"Actor": {child_id: "User"}})
    docs[1]["id"] = child_id
    (query,) = convert_documents(docs)
    assert "| mv-expand _sigma_child_context_row" in query
    assert '["Actor"]' in query


@pytest.mark.parametrize("kind", ["value_sum", "value_avg", "value_percentile"])
def test_numeric_child_context_consumption_remains_actionable_until_numeric_slice(kind):
    docs = documents(kind, groups=(), field="User")
    docs[-1]["correlation"]["condition"]["field"] = "User"
    with pytest.raises(
        SigmaConversionError, match="numeric parents consuming retained child context.*scalar child metrics"
    ):
        convert_documents(docs)
