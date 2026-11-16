"""Dependency and diagnostic contracts at the public collection conversion seam."""

from copy import deepcopy
from itertools import permutations

import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaError
from sigma.processing.pipeline import ProcessingPipeline
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def collection(documents):
    return SigmaCollection.from_yaml(yaml.safe_dump_all(documents))


def backend(**options):
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    from tests.correlation_helpers import fixture_schema

    pipeline.vars["kusto_schema"] = fixture_schema()
    return KustoBackend(processing_pipeline=pipeline, **{"correlation_timestamp_field": "Timestamp", **options})


def documents():
    return [source_document(), correlation_document("count", "event_count", ["source-rule"])]


@pytest.mark.parametrize("method", ["fixed", "DEFAULT", "", False, []])
def test_unsupported_correlation_method_fails_with_supported_selection(method):
    with pytest.raises(SigmaError, match="correlation method.*default"):
        backend().convert(collection(documents()), correlation_method=method)


@pytest.mark.parametrize("timestamp", [42, True, [], {}, "   "])
def test_invalid_timestamp_configuration_names_the_option_and_required_type(timestamp):
    with pytest.raises(SigmaError, match="correlation_timestamp_field.*nonempty.*string"):
        backend(correlation_timestamp_field=timestamp).convert(collection(documents()))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unavailable", "source-rule.*conversion.*unavailable.*convert"),
        ("invalid-state", "source-rule.*invalid conversion state"),
        ("invalid-query", "source-rule.*nonempty KQL search strings"),
        ("invalid-table", "source-rule.*mapped query table.*string"),
    ],
)
def test_unusable_source_metadata_identifies_source_and_recovery(mutation, message):
    docs = documents()
    docs.insert(1, source_document("trigger"))
    docs[-1]["correlation"]["rules"].append("trigger")
    rules = collection(docs)

    def invalidate(rule, output_format, index, condition, result):
        if rule.name == "trigger":
            source = next(item for item in rules.rules if item.name == "source-rule")
            if mutation == "unavailable":
                source.set_conversion_states(None)
            elif mutation == "invalid-state":
                source.set_conversion_states([None])
            elif mutation == "invalid-query":
                source.set_conversion_result([42])
            else:
                source.get_conversion_states()[0].processing_state["query_table"] = ["Events"]
        return result

    with pytest.raises(SigmaError, match=message):
        backend().convert(rules, callback=invalidate)


def dependency_documents(generate):
    source = source_document()
    source["id"] = "11111111-1111-1111-1111-111111111111"
    child = correlation_document("child", "event_count", [source["id"]], condition={"gte": 1}, generate=generate)
    child["id"] = "22222222-2222-2222-2222-222222222222"
    middle = correlation_document("middle", "event_count", ["child"], condition={"gte": 1}, generate=generate)
    middle["id"] = "33333333-3333-3333-3333-333333333333"
    parent = correlation_document("parent", "temporal", [middle["id"], "child", "direct"], generate=generate)
    sibling = correlation_document("sibling", "event_count", [child["id"]], condition={"gte": 1}, generate=generate)
    return [source, child, middle, parent, sibling, source_document("direct", product="network")]


@pytest.mark.parametrize("generate", [False, True])
def test_shared_multilevel_mixed_name_id_dependencies_are_order_independent(generate):
    docs = dependency_documents(generate)
    unrelated = source_document("unrelated", marker="unrelated")
    expected = sorted(convert_documents(deepcopy([*docs, unrelated])))
    # Every ordering of the four correlation documents, each with raw sources
    # interleaved at different depths; output order itself follows collection order.
    for ordered in permutations(docs[1:5]):
        permuted = [ordered[0], docs[5], ordered[1], unrelated, ordered[2], docs[0], ordered[3]]
        assert sorted(convert_documents(deepcopy(permuted))) == expected


def test_unresolved_reference_reports_the_missing_reference():
    with pytest.raises(SigmaError, match="missing-source"):
        backend().convert(collection([correlation_document("count", "event_count", ["missing-source"])]))


@pytest.mark.parametrize("metadata", ["kusto_result_fields", "kusto_result_timestamp"])
def test_child_metadata_failure_identifies_child_and_keys_to_preserve(metadata):
    rules = collection(
        [
            source_document(),
            correlation_document("child", "event_count", ["source-rule"]),
            correlation_document("parent", "event_count", ["child"]),
        ]
    )

    def invalidate(rule, output_format, index, condition, result):
        if rule.name == "child":
            rule.get_conversion_states()[0].processing_state[metadata] = 42
        return result

    with pytest.raises(
        SigmaError, match="child.*schema and timestamp metadata.*kusto_result_fields.*kusto_result_timestamp"
    ):
        backend().convert(rules, callback=invalidate)


def test_dependency_runtime_fixtures_remain_deterministically_generated():
    from pathlib import Path

    from tests.fixtures.correlation.dependency_order import fixtures

    for name, query in fixtures().items():
        assert Path(__file__).with_name("fixtures").joinpath("correlation", name).read_text() == query


@pytest.mark.parametrize("mutation", ["missing-checks", "empty-checks", "changed-raw", "append-filter"])
def test_incoherent_child_raw_metadata_keeps_checked_public_query(mutation):
    docs = [
        source_document(),
        correlation_document("child", "value_sum", ["source-rule"], groups=(), field="Amount", condition={"gte": 1}),
        correlation_document("parent", "event_count", ["child"], groups=(), condition={"gte": 1}),
    ]
    checked = []

    def modify(rule, output_format, index, condition, result):
        if rule.name == "child":
            checked.append(result)
            metadata = rule.get_conversion_states()[0].processing_state
            if mutation == "missing-checks":
                metadata.pop("kusto_guard_queries")
            elif mutation == "empty-checks":
                metadata.update(
                    kusto_guard_queries=(),
                    kusto_guard_error_columns=(),
                    kusto_guard_messages=(),
                    kusto_error_column=None,
                )
            elif mutation == "changed-raw":
                metadata["kusto_raw_query"] += "\n| take 0"
            else:
                return result + "\n| take 1"
        return result

    (query,) = backend().convert(collection(docs), callback=modify)

    assert checked[0] in query
    assert "| where assert(" in query
    if mutation == "append-filter":
        assert checked[0] + "\n| take 1" in query


def test_callback_cannot_substitute_unchecked_raw_child_for_checked_query():
    docs = [
        source_document(),
        correlation_document("child", "value_sum", ["source-rule"], groups=(), field="Amount", condition={"gte": 1}),
        correlation_document("parent", "event_count", ["child"], groups=()),
    ]

    def remove_validation(rule, output_format, index, condition, result):
        if rule.name == "child":
            return rule.get_conversion_states()[0].processing_state["kusto_raw_query"]
        return result

    with pytest.raises(SigmaError, match="child correlation.*validation metadata.*preserve the checked public"):
        backend().convert(collection(docs), callback=remove_validation)
