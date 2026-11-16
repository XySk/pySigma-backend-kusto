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
            "burst-size",
            "value_count",
            ["failed-logon-bursts"],
            field="FailureCount",
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
    assert '["EventCount"]' in query
    assert '["FailureCount"]' in query


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


def test_targeted_account_example_preserves_security_events_and_groups_across_computers():
    from tests.fixtures.correlation.count_existence import targeted_queries

    query, general = targeted_queries()
    assert "EventID == 4625" in query and "EventID == 4624" in query
    assert '["TargetUserName"]' in query
    assert '["SubjectUserName"]' not in query
    assert "GroupComputer" not in query
    assert "HasFailedLogonBursts" in query
    assert "let FailedLogonBursts = () {" in query
    assert "LastFailedLogonBurstTime" in query
    assert "EventCount =" not in query
    assert "EventCount = row_cumsum(" in general


def test_count_existence_fixtures_match_current_public_conversion():
    from pathlib import Path

    from tests.fixtures.correlation.count_existence import fixtures

    directory = Path(__file__).parent / "fixtures" / "correlation"
    for name, query in fixtures().items():
        assert (directory / name).read_text() == query


def test_predecessor_offset_at_verified_engine_limit_remains_specialized():
    (query,) = convert_documents(chain_documents({"gte": 1025}))
    assert re.search(r"prev\(EventTime(?:_\d+)?, 1024\)", query)
    assert "ThresholdPredecessorTime" in query


def test_predecessor_offset_beyond_verified_engine_limit_keeps_general_plan():
    for condition in ({"gte": 1026}, {"gt": 1025}, {"gte": 10000}, {"gte": 2_147_483_649}):
        (query,) = convert_documents(chain_documents(condition))
        assert "EventCount = row_cumsum(" in query
        assert "ThresholdPredecessorTime" not in query


def test_predecessor_real_group_comparison_preserves_native_nan_identity():
    from tests.correlation_helpers import fixture_schema

    documents = chain_documents()
    for document in documents[2:]:
        document["correlation"]["group-by"] = ["Group"]
    (query,) = convert_documents(documents, schema=fixture_schema(Group="real"))
    assert "isnan(GroupGroup)" in query
    assert "isnan(prev(GroupGroup, 2))" in query


def test_context_inherited_from_sibling_can_require_the_child_metric():
    documents = chain_documents()
    documents[1]["fields"] = ["Amount"]
    documents[3]["correlation"]["aliases"] = {
        "FailureCount": {"failed-logon-bursts": "EventCount", "successful-logon": "Amount"}
    }
    (query,) = convert_documents(documents)
    assert "EventCount = row_cumsum(" in query
    assert '["FailureCount"]' in query


def test_existence_plan_preserves_reserved_child_metric_group_diagnostic():
    import pytest

    from sigma.exceptions import SigmaConversionError

    documents = chain_documents()
    documents[2]["correlation"]["group-by"] = ["EventCount"]
    documents[3]["correlation"]["group-by"] = ["EventCount"]
    with pytest.raises(SigmaConversionError, match="grouping fields conflict.*EventCount"):
        convert_documents(documents)


def test_reusing_backend_resets_graph_requirements_even_after_conversion_failure():
    import pytest
    import yaml

    from sigma.backends.kusto import KustoBackend
    from sigma.collection import SigmaCollection
    from sigma.exceptions import SigmaConversionError
    from sigma.processing.pipeline import ProcessingPipeline
    from tests.correlation_helpers import fixture_schema

    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    pipeline.vars["kusto_schema"] = fixture_schema()
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp")

    def collection(documents):
        return SigmaCollection.from_yaml(yaml.safe_dump_all(documents))

    (full,) = backend.convert(collection(chain_documents()), callback=lambda rule, output, index, cond, query: query)
    assert "EventCount = row_cumsum(" in full
    invalid = chain_documents()
    invalid[3]["correlation"]["group-by"] = ["Missing"]
    with pytest.raises(SigmaConversionError):
        backend.convert(collection(invalid))
    (specialized,) = backend.convert(collection(chain_documents()))
    assert "ThresholdPredecessorTime" in specialized
    assert "EventCount =" not in specialized
