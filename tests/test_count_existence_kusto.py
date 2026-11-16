"""Consumer-aware count plans through ordinary collection conversion."""

import re

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def chain_documents(condition=None):
    return [
        source_document("failed-logon", "failure"),
        source_document("successful-logon", "success"),
        correlation_document("failed-logon-bursts", "event_count", ["failed-logon"], condition=condition or {"gte": 3}),
        correlation_document("failures-then-success", "temporal_ordered", ["failed-logon-bursts", "successful-logon"]),
    ]


def test_existence_only_child_counts_individual_matches_before_deduplicating_timestamps():
    documents = chain_documents()
    documents[0]["detection"]["alternate"] = {"Marker": "failure"}
    documents[0]["detection"]["condition"] = ["selection", "alternate"]

    (query,) = convert_documents(documents)

    assert re.search(r"prev\(EventTime(?:_\d+)?, 2\)", query)
    assert "prev(GroupHost, 2)" in query
    assert query.count('Marker =~ "failure"') == 2
    assert query.index("prev(EventTime") < query.index("| distinct EventTime")
    assert "SourceMatchCount = count()" not in query
    assert "EventCount =" not in query
    assert "CountBoundary" not in query


def test_threshold_one_child_needs_neither_predecessor_sort_nor_metric():
    (query,) = convert_documents(chain_documents({"gte": 1}))
    child = query.split("};", 1)[0]
    assert "prev(" not in child
    assert "| sort " not in child
    assert "| distinct " in child
    assert "EventCount" not in child


def test_strict_integer_lower_bound_uses_next_match_threshold():
    (query,) = convert_documents(chain_documents({"gt": 2}))
    assert re.search(r"prev\(EventTime(?:_\d+)?, 2\)", query)


def test_fractional_and_non_lower_bound_children_preserve_actual_counts():
    for condition in ({"gte": 2.5}, {"eq": 3}, {"neq": 3}, {"lte": 3}, {"lt": 3}, {"gte": 0}):
        (query,) = convert_documents(chain_documents(condition))
        assert "EventCount = row_cumsum(" in query
        assert "ThresholdPredecessorTime" not in query


def test_shared_consumer_of_metric_retains_full_child_for_every_parent_in_any_document_order():
    documents = chain_documents()
    documents.append(
        correlation_document(
            "burst-size", "value_count", ["failed-logon-bursts"], field="FailureCount",
            aliases={"FailureCount": {"failed-logon-bursts": "EventCount"}},
        )
    )
    for ordered in (documents, list(reversed(documents))):
        queries = convert_documents(ordered)
        assert len(queries) == 2
        for query in queries:
            assert "EventCount = row_cumsum(" in query
            assert "ThresholdPredecessorTime" not in query


def test_multilevel_context_request_for_child_metric_retains_full_plan():
    documents = chain_documents()
    documents[3]["fields"] = ["FailureCount"]
    documents[3]["correlation"]["aliases"] = {
        "FailureCount": {"failed-logon-bursts": "EventCount", "successful-logon": "Amount"}
    }
    documents.append(correlation_document("outer", "value_count", ["failures-then-success"], field="FailureCount"))
    (query,) = convert_documents(documents)
    assert "EventCount = row_cumsum(" in query
    assert 'pack_array(gettype(["EventCount"]), ["EventCount"])' in query


def test_requested_source_context_retains_full_child_contributors():
    documents = chain_documents()
    documents[0]["fields"] = ["User"]
    (query,) = convert_documents(documents)
    assert "EventCount = row_cumsum(" in query
    assert "ThresholdPredecessorTime" not in query


def test_standalone_child_generation_keeps_actual_count():
    documents = chain_documents()
    documents[-1]["correlation"]["generate"] = True
    queries = convert_documents(documents)
    assert len(queries) == 3
    assert "EventCount = row_cumsum(" in queries[1]
    assert "EventCount = row_cumsum(" in queries[-1]


def test_callback_observes_actual_child_counts_and_can_modify_its_output():
    observed = []

    def callback(rule, output_format, index, condition, query):
        if rule.name == "failed-logon-bursts":
            observed.append(query)
            return query + "\n| where EventCount >= 4"
        return query

    (query,) = convert_documents(chain_documents(), callback=callback)
    assert len(observed) == 1
    assert "EventCount = row_cumsum(" in observed[0]
    assert "| where EventCount >= 4" in query
