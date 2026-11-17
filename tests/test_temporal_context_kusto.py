"""Temporal chains retain requested fields through public collection conversion."""

import pytest

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def chain(kind="temporal"):
    source = source_document()
    source["fields"] = ["User", "Server"]
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    middle = correlation_document("middle", kind, ["child"], groups=("User",))
    if kind == "boolean":
        middle["correlation"]["type"] = "temporal"
        middle["correlation"]["condition"] = "child"
    parent = correlation_document("parent", "temporal", ["middle"], groups=("User", "Server"))
    return [source, child, middle, parent]


@pytest.mark.parametrize("kind", ["temporal", "temporal_ordered", "boolean"])
def test_temporal_chain_carries_inherited_requests_and_scalar_group_relationships(kind):
    (query,) = convert_documents(chain(kind))
    assert '["Server"]' in query
    assert '["WindowEnd"]' in query


@pytest.mark.parametrize(
    "producer",
    ["event_count", "value_count", "value_sum", "value_avg", "value_percentile", "temporal", "temporal_ordered"],
)
@pytest.mark.parametrize("consumer", ["temporal", "temporal_ordered", "boolean"])
def test_temporal_context_consumers_accept_supported_producer_summaries(producer, consumer):
    docs = chain(consumer)
    child = correlation_document(
        "child",
        producer,
        ["source-rule"],
        condition={"gte": 1, **({"field": "Amount"} if producer == "value_avg" else {})},
    )
    if producer in ("temporal", "temporal_ordered"):
        child["correlation"].pop("condition", None)
    docs[1] = child
    assert len(convert_documents(docs)) == 1


@pytest.mark.parametrize("kind", ["temporal", "temporal_ordered", "boolean"])
def test_temporal_parent_keeps_shared_child_identity_and_generate(kind):
    docs = chain(kind)
    docs[1]["correlation"]["generate"] = True
    sibling = correlation_document("sibling", "temporal", ["child"], groups=("User",))
    docs.insert(-1, sibling)
    docs[-1]["correlation"]["rules"].append("sibling")
    queries = convert_documents(docs)
    assert len(queries) == 2
    assert '"middle"' in queries[-1]
    assert '"sibling"' in queries[-1]


def test_temporal_aliases_for_child_context_and_metrics_use_returned_logical_schema():
    docs = chain()
    docs[2]["correlation"]["group-by"] = ["Actor", "Total"]
    docs[2]["correlation"]["aliases"] = {"Actor": {"child": "User"}, "Total": {"child": "EventCount"}}
    docs[-1]["correlation"]["group-by"] = ["Actor", "Server"]
    assert len(convert_documents(docs)) == 1


def test_temporal_missing_child_field_is_actionable():
    from sigma.exceptions import SigmaConversionError

    docs = chain()
    docs[2]["correlation"]["group-by"] = ["Missing"]
    with pytest.raises(SigmaConversionError, match="does not return required field 'Missing'.*Available fields"):
        convert_documents(docs)
