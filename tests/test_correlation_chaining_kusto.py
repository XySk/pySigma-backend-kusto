"""Chained correlation contracts through parsed collection conversion."""

import pytest

from sigma.exceptions import SigmaError
from sigma.pipelines.kusto_common.schema import FieldMappings
from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.test_correlation_kusto import final_summary_projection, summary_data_query, summary_projections


def test_value_count_parent_counts_child_value_metric_as_one_exact_value_per_summary():
    documents = [
        source_document(),
        correlation_document("child", "value_count", ["source-rule"]),
        correlation_document("parent", "value_count", ["child"], field="ValueCount"),
    ]

    (query,) = convert_documents(documents)

    assert 'EventTime = ["WindowEnd"]' in query
    assert 'CountedValueCount = ["ValueCount"]' in query
    assert summary_data_query(query).count("ValueCount = row_cumsum(") == 2
    assert final_summary_projection(query).endswith('["Host"] = GroupHost, ValueCount')


def test_ordered_parent_scopes_complete_temporal_child_before_strict_window_end_sequence():
    documents = [
        source_document(),
        source_document("second-rule"),
        source_document("direct-rule", product="network"),
        correlation_document("child", "temporal", ["source-rule", "second-rule"]),
        correlation_document("parent", "temporal_ordered", ["child", "direct-rule"]),
    ]

    (query,) = convert_documents(documents)

    assert "union (Events\n" in summary_data_query(query)
    assert "union (Child()\n" in summary_data_query(query)
    assert '["WindowEnd"], GroupHost = ["Host"]' in query
    assert '["Timestamp"], GroupHost = ["Host"]' in query
    assert 'countif(SourceRule == "child") > 0' in query
    assert 'countif(SourceRule == "direct-rule") > 0' in query
    assert "| where CompleteNow" in query
    assert final_summary_projection(query).endswith('["Host"] = GroupHost, RuleCount = tolong(2)')


TYPES = ["event_count", "value_count", "value_sum", "value_percentile", "temporal", "temporal_ordered"]
METRICS = {
    "event_count": "EventCount",
    "value_count": "ValueCount",
    "value_sum": "ValueSum",
    "value_percentile": "ValuePercentile",
    "temporal": "RuleCount",
    "temporal_ordered": "RuleCount",
}


@pytest.mark.parametrize("producer", TYPES)
@pytest.mark.parametrize("consumer", TYPES)
def test_all_supported_types_produce_and_consume_child_summaries(producer, consumer):
    documents = [
        source_document(),
        source_document("second-rule"),
        correlation_document("child", producer, ["source-rule", "second-rule"]),
        correlation_document("parent", consumer, ["child"], field=METRICS[producer]),
    ]

    (query,) = convert_documents(documents)

    assert len(summary_projections(query)) == 2
    producer_metric = "RuleCount = tolong(2)" if producer == "temporal_ordered" else METRICS[producer]
    if producer == "event_count" and consumer in ("event_count", "temporal", "temporal_ordered"):
        assert "EventCount" not in summary_projections(query)[0]
    else:
        assert summary_projections(query)[0].endswith(f", {producer_metric}")
    parent = summary_data_query(query).split("\n};\n", 1)[1]
    timestamp, group, source = (
        ("EventTime", "GroupHost", "SourceRule")
        if consumer in ("event_count", "value_count", "value_sum", "value_percentile", "temporal", "temporal_ordered")
        else ("_sigma_timestamp", "_sigma_group_0", "_sigma_rule")
    )
    assert f'{timestamp} = ["WindowEnd"], {group} = ["Host"]' in parent
    if consumer not in ("event_count", "value_count", "value_sum", "value_percentile"):
        assert f'{source} = "child"' in parent
    consumer_metric = "RuleCount = tolong(1)" if consumer == "temporal_ordered" else METRICS[consumer]
    assert final_summary_projection(query).endswith(f", {consumer_metric}")
    if consumer in ("value_count", "value_sum", "value_percentile"):
        measured = f'["{METRICS[producer]}"]'
        expected_value = (
            measured
            if consumer in ("value_count", "value_sum", "value_percentile")
            else f"pack_array(gettype({measured}), {measured})"
        )
        column = (
            f"Counted{METRICS[producer]}"
            if consumer == "value_count"
            else f"Measured{METRICS[producer]}" if consumer in ("value_sum", "value_percentile") else "_sigma_value"
        )
        assert f"{column} = {expected_value}" in parent
    if consumer == "event_count":
        assert "SourceMatchCount = count()" in parent
        assert "row_cumsum(SourceMatchDelta" in parent


@pytest.mark.parametrize("producer", TYPES)
def test_child_window_fields_group_and_actual_metric_can_all_be_aliased_without_detection_mapping(producer):
    child = correlation_document("child", producer, ["source-rule"])
    child["id"] = "22222222-2222-2222-2222-222222222222"
    parent = correlation_document(
        "parent",
        "value_count",
        [child["id"]],
        field=["ChildStart", "ChildEnd", "ChildMetric", "Host"],
        aliases={
            "ChildStart": {"child": "WindowStart"},
            "ChildEnd": {"child": "WindowEnd"},
            "ChildMetric": {"child": METRICS[producer]},
        },
    )
    mappings = FieldMappings(
        generic_mappings={
            "Timestamp": "PhysicalTime",
            "Host": "PhysicalHost",
            "WindowEnd": "WrongEnd",
            "WindowStart": "WrongStart",
            METRICS[producer]: "WrongMetric",
        }
    )

    (query,) = convert_documents([source_document(), child, parent], mappings=mappings)

    parent_query = query.split("\n};\n", 1)[1]
    assert 'EventTime = ["WindowEnd"]' in parent_query
    for column, field in zip(
        ["ChildStart", "ChildEnd", "ChildMetric", "Host"], ["WindowStart", "WindowEnd", METRICS[producer], "Host"]
    ):
        assert f'Counted{column} = ["{field}"]' in parent_query
    assert "Wrong" not in query
    assert '["PhysicalTime"]' in query
    assert '["PhysicalHost"]' in query


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_value_parent_counts_child_metric_and_host_independently_with_and(operator, query_operator):
    documents = [
        source_document(),
        correlation_document("child", "event_count", ["source-rule"], groups=("Case", "Host")),
        correlation_document(
            "parent", "value_count", ["child"], groups=("Case",), field=["EventCount", "Host"], condition={operator: 2}
        ),
    ]

    (query,) = convert_documents(documents)

    assert 'CountedEventCount = ["EventCount"], CountedHost = ["Host"]' in query
    assert '["ValueCount_EventCount"] = row_cumsum(CountedEventCountDelta' in query
    assert '["ValueCount_Host"] = row_cumsum(CountedHostDelta' in query
    assert f'| where ["ValueCount_EventCount"] {query_operator} 2 and ["ValueCount_Host"] {query_operator} 2\n' in query
    assert final_summary_projection(query).endswith(
        '["Case"] = GroupCase, ["ValueCount_EventCount"], ["ValueCount_Host"]'
    )


def test_multifield_child_exposes_each_metric_for_counting_without_an_invented_valuecount():
    documents = [
        source_document(),
        correlation_document("child", "value_count", ["source-rule"], field=["User", "Target"]),
        correlation_document("parent", "value_count", ["child"], field=["ValueCount_User", "ValueCount_Target"]),
    ]

    (query,) = convert_documents(documents)

    assert 'CountedValueCount_User = ["ValueCount_User"], CountedValueCount_Target = ["ValueCount_Target"]' in query
    assert final_summary_projection(query).endswith(
        '["Host"] = GroupHost, ["ValueCount_ValueCount_User"], ["ValueCount_ValueCount_Target"]'
    )


def test_temporal_parent_counts_immediate_child_and_detection_identities():
    documents = [
        source_document(),
        source_document("second-rule"),
        source_document("direct-rule"),
        correlation_document("child", "value_count", ["source-rule", "second-rule"]),
        correlation_document("parent", "temporal", ["child", "direct-rule"]),
    ]

    (query,) = convert_documents(documents)

    parent = summary_data_query(query).split("\n};\n", 1)[1]
    assert 'SourceRule = "child"' in parent
    assert 'SourceRule = "direct-rule"' in parent
    assert "source-rule" not in parent
    assert "second-rule" not in parent
    assert "RuleCount = tolong(HasChild) + tolong(HasDirectrule)" in parent
    assert "| where RuleCount >= 2\n" in parent


def test_ordered_parent_repeated_child_name_and_id_require_separate_times_but_one_type():
    child = correlation_document("child", "temporal_ordered", ["source-rule"])
    child["id"] = "22222222-2222-2222-2222-222222222222"
    parent = correlation_document("parent", "temporal_ordered", ["child", child["id"]])

    (query,) = convert_documents([source_document(), child, parent])

    assert 'countif(SourceRule == "child") > 0' in query
    assert "| where CompleteNow" in query
    assert final_summary_projection(query).endswith("RuleCount = tolong(1)")


@pytest.mark.parametrize("condition", [{"gte": 1}, {"lte": 2}, {"eq": 1}])
def test_ordered_parent_preserves_full_sequence_condition_restriction_with_child_sources(condition):
    documents = [
        source_document(),
        source_document("direct-rule"),
        correlation_document("child", "event_count", ["source-rule"]),
        correlation_document("parent", "temporal_ordered", ["child", "direct-rule"], condition=condition),
    ]

    with pytest.raises(SigmaError, match="temporal_ordered requires the full declared sequence"):
        convert_documents(documents)


@pytest.mark.parametrize(
    ("producer", "field", "groups", "aliases"),
    [
        ("value_count", "EventCount", ("Host",), {}),
        ("temporal", "EventCount", ("Host",), {}),
        ("temporal_ordered", "User", ("Host",), {}),
        ("event_count", ["EventCount", "User"], ("Host",), {}),
        ("value_count", "ValueCount", ("MissingHost",), {"MissingHost": {"child": "RemovedHost"}}),
        ("value_count", "Count", ("Host",), {"Count": {"child": "ValueCount_User"}}),
    ],
)
def test_consumers_fail_clearly_for_fields_absent_from_actual_child_result(producer, field, groups, aliases):
    documents = [
        source_document(),
        correlation_document("child", producer, ["source-rule"]),
        correlation_document("parent", "value_count", ["child"], field=field, groups=groups, aliases=aliases),
    ]
    missing = (
        "RemovedHost"
        if "MissingHost" in groups
        else "ValueCount_User" if aliases else (field[-1] if isinstance(field, list) else field)
    )

    with pytest.raises(SigmaError, match=f"child.*does not return required field.*{missing}"):
        convert_documents(documents)


def multilevel_documents(*, child_generate=False, middle_generate=False, parent_generate=False):
    return [
        source_document(),
        source_document("direct-rule", product="network", marker="direct"),
        correlation_document(
            "child", "event_count", ["source-rule"], groups=("Case", "Host"), span="2m", generate=child_generate
        ),
        correlation_document(
            "middle",
            "value_count",
            ["child"],
            field="EventCount",
            groups=("Tenant",),
            span="3m",
            aliases={"Tenant": {"child": "Case"}},
            generate=middle_generate,
        ),
        correlation_document(
            "parent",
            "temporal_ordered",
            ["middle", "direct-rule"],
            groups=("Tenant",),
            span="4m",
            aliases={"Tenant": {"middle": "Tenant", "direct-rule": "Scope"}},
            generate=parent_generate,
        ),
    ]


def test_multilevel_mixed_sources_keep_result_names_and_scope_bindings_separate_from_detection_tables():
    mappings = FieldMappings(
        table_mappings={
            "Events": {
                "Timestamp": "ProcessTime",
                "Case": "ProcessCase",
                "Host": "ProcessHost",
                "WindowEnd": "WrongEnd",
                "EventCount": "WrongEvents",
                "ValueCount": "WrongValues",
            },
            "NetworkEvents": {"Timestamp": "NetworkTime", "Scope": "NetworkScope", "ValueCount": "WrongValue"},
        }
    )

    (query,) = convert_documents(multilevel_documents(), mappings=mappings)

    assert len(summary_projections(query)) == 3
    assert summary_projections(query)[0].endswith(', ["Case"] = GroupCase, ["Host"] = GroupHost, EventCount')
    assert summary_projections(query)[1].endswith(', ["Tenant"] = GroupTenant, ValueCount')
    assert "let ValueCountSources = materialize(Child()" in query
    assert "_sigma_correlation_error" not in summary_data_query(query)
    assert "assert(" not in query
    assert "_sigma_identity" not in query
    assert 'let SourceMatchArrivals = Events\n| where Marker =~ "match"' in query
    assert '(NetworkEvents\n| where Marker =~ "direct"' in query
    assert '["ProcessTime"]' in query
    assert '["NetworkTime"]' in query
    assert '["ProcessCase"]' in query
    assert '["NetworkScope"]' in query
    assert '["Case"]' in query
    assert '["Tenant"]' in query
    assert '["EventCount"]' in query
    assert summary_data_query(query).count('["WindowEnd"]') == 2
    assert "Wrong" not in query
    assert "ago(" not in query
    assert ">= EventTime_1 - 4m" in query
    assert "| where CompleteNow" in query
    assert final_summary_projection(query).endswith('["Tenant"] = GroupTenant, RuleCount = tolong(2)')


def test_chain_bindings_do_not_shadow_known_tables_in_direct_or_descendant_sources():
    (query,) = convert_documents(multilevel_documents(), tables=("Child", "Middle"))

    # Scoped child functions must avoid raw tables at every descendant level.
    assert "let Child_1 = () {\n" in query
    assert "let Middle_1 = () {\n" in query
    assert "let ValueCountSources = materialize(Child_1()" in query
    assert 'let SourceMatchArrivals = Child\n| where Marker =~ "match"' in query
    assert '(Middle\n| where Marker =~ "direct"' in query
    assert "let Child =" not in query
    assert "let Middle =" not in query
    assert "| where CompleteNow" in query


@pytest.mark.parametrize(
    ("child_generate", "middle_generate", "parent_generate", "output_metrics"),
    [
        (False, False, False, ["RuleCount = tolong(2)"]),
        (False, False, True, ["direct", "ValueCount", "RuleCount = tolong(2)"]),
        (False, True, False, ["EventCount", "RuleCount = tolong(2)"]),
        (True, False, False, ["detection", "RuleCount = tolong(2)"]),
        (True, True, True, ["detection", "direct", "EventCount", "ValueCount", "RuleCount = tolong(2)"]),
    ],
)
def test_multilevel_generation_preserves_embedded_summaries_and_emits_immediate_sources_as_configured(
    child_generate, middle_generate, parent_generate, output_metrics
):
    queries = convert_documents(
        multilevel_documents(
            child_generate=child_generate, middle_generate=middle_generate, parent_generate=parent_generate
        )
    )

    assert len(queries) == len(output_metrics)
    for query, metric in zip(queries, output_metrics):
        if metric == "detection":
            assert query == 'Marker =~ "match"'
        elif metric == "direct":
            assert query == 'Marker =~ "direct"'
        else:
            assert final_summary_projection(query).endswith(metric)
    assert len(summary_projections(queries[-1])) == 3


@pytest.mark.parametrize(
    ("first_generate", "second_generate", "count"), [(True, True, 3), (False, True, 2), (True, False, 2)]
)
def test_shared_child_remains_available_to_each_parent_and_any_consumer_can_suppress_standalone_output(
    first_generate, second_generate, count
):
    documents = [
        source_document(),
        correlation_document("child", "temporal_ordered", ["source-rule"]),
        correlation_document("first-parent", "event_count", ["child"], generate=first_generate),
        correlation_document("second-parent", "value_count", ["child"], field="RuleCount", generate=second_generate),
    ]

    queries = convert_documents(documents)

    assert len(queries) == count
    assert final_summary_projection(queries[-2]).endswith("EventCount")
    assert final_summary_projection(queries[-1]).endswith("ValueCount")
    for parent in queries[-2:]:
        assert summary_data_query(parent).count("let Child = () {") == 1
        assert '["WindowEnd"]' in parent
