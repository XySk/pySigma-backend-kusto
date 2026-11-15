"""Chained correlation contracts through parsed collection conversion."""

import pytest

from sigma.exceptions import SigmaError
from sigma.pipelines.kusto_common.schema import FieldMappings
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def test_value_count_parent_counts_child_value_metric_as_one_exact_value_per_summary():
    documents = [
        source_document(),
        correlation_document("child", "value_count", ["source-rule"]),
        correlation_document("parent", "value_count", ["child"], field="ValueCount"),
    ]

    (query,) = convert_documents(documents)

    assert '_sigma_timestamp = todatetime(["WindowEnd"])' in query
    assert '_sigma_value = pack_array(gettype(["ValueCount"]), ["ValueCount"])' in query
    assert query.count("ValueCount = count_distinctif(_sigma_value, isnotempty(_sigma_value))") == 2
    assert query.endswith('["Host"] = _sigma_group_0, ValueCount')


def test_ordered_parent_scopes_complete_temporal_child_before_strict_window_end_sequence():
    documents = [
        source_document(),
        source_document("second-rule"),
        source_document("direct-rule", product="network"),
        correlation_document("child", "temporal", ["source-rule", "second-rule"]),
        correlation_document("parent", "temporal_ordered", ["child", "direct-rule"]),
    ]

    (query,) = convert_documents(documents)

    assert query.startswith("let _sigma_child_0 = () {\nlet _sigma_types = union (Events\n")
    assert "RuleCount\n};\nlet _sigma_types = union (_sigma_child_0()\n" in query
    assert '_sigma_timestamp = todatetime(["WindowEnd"]), _sigma_group_0 = tostring(["Host"])' in query
    assert '_sigma_timestamp = todatetime(["Timestamp"]), _sigma_group_0 = tostring(["Host"])' in query
    assert '| where _sigma_rule == "child"' in query
    assert '| where _sigma_rule == "direct-rule"' in query
    assert "| where _sigma_timestamp > _sigma_sequence_end and _sigma_timestamp <= _sigma_sequence_start + 5m" in query
    assert query.endswith('["Host"] = _sigma_group_0, RuleCount = tolong(2)')


TYPES = ["event_count", "value_count", "value_sum", "temporal", "temporal_ordered"]
METRICS = {
    "event_count": "EventCount",
    "value_count": "ValueCount",
    "value_sum": "ValueSum",
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

    assert query.startswith("let _sigma_child_0 = () {\n")
    assert f", {METRICS[producer]}" in query.split("\n};\n")[0].splitlines()[-1]
    parent = query.split("\n};\n", 1)[1]
    assert '_sigma_timestamp = todatetime(["WindowEnd"]), _sigma_group_0 = tostring(["Host"])' in parent
    assert '| extend _sigma_rule = "child"' in parent
    assert f", {METRICS[consumer]}" in parent.splitlines()[-1]
    if consumer in ("value_count", "value_sum"):
        assert f'_sigma_value = pack_array(gettype(["{METRICS[producer]}"]), ["{METRICS[producer]}"])' in parent
    if consumer == "event_count":
        assert "_sigma_weight = count()" in parent
        assert "sum(_sigma_weight)" in parent


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
    assert '_sigma_timestamp = todatetime(["WindowEnd"])' in parent_query
    for column, field in enumerate(["WindowStart", "WindowEnd", METRICS[producer], "Host"]):
        assert f'_sigma_value_{column} = pack_array(gettype(["{field}"]), ["{field}"])' in parent_query
    assert "Wrong" not in query
    assert 'todatetime(["PhysicalTime"])' in query
    assert 'tostring(["PhysicalHost"])' in query


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

    assert (
        '_sigma_value_0 = pack_array(gettype(["EventCount"]), ["EventCount"]), _sigma_value_1 = pack_array(gettype(["Host"]), ["Host"])'
        in query
    )
    assert '["ValueCount_EventCount"] = count_distinctif(_sigma_value_0, isnotempty(_sigma_value_0))' in query
    assert '["ValueCount_Host"] = count_distinctif(_sigma_value_1, isnotempty(_sigma_value_1))' in query
    assert f'| where ["ValueCount_EventCount"] {query_operator} 2 and ["ValueCount_Host"] {query_operator} 2\n' in query
    assert query.endswith('["Case"] = _sigma_group_0, ["ValueCount_EventCount"], ["ValueCount_Host"]')


def test_multifield_child_exposes_each_metric_for_counting_without_an_invented_valuecount():
    documents = [
        source_document(),
        correlation_document("child", "value_count", ["source-rule"], field=["User", "Target"]),
        correlation_document("parent", "value_count", ["child"], field=["ValueCount_User", "ValueCount_Target"]),
    ]

    (query,) = convert_documents(documents)

    assert (
        '_sigma_value_0 = pack_array(gettype(["ValueCount_User"]), ["ValueCount_User"]), _sigma_value_1 = pack_array(gettype(["ValueCount_Target"]), ["ValueCount_Target"])'
        in query
    )
    assert query.endswith('["Host"] = _sigma_group_0, ["ValueCount_ValueCount_User"], ["ValueCount_ValueCount_Target"]')


def test_temporal_parent_counts_immediate_child_and_detection_identities():
    documents = [
        source_document(),
        source_document("second-rule"),
        source_document("direct-rule"),
        correlation_document("child", "value_count", ["source-rule", "second-rule"]),
        correlation_document("parent", "temporal", ["child", "direct-rule"]),
    ]

    (query,) = convert_documents(documents)

    parent = query.split("\n};\n", 1)[1]
    assert '| extend _sigma_rule = "child"' in parent
    assert '| extend _sigma_rule = "direct-rule"' in parent
    assert "source-rule" not in parent
    assert "second-rule" not in parent
    assert "RuleCount = count_distinctif(_sigma_rule, isnotempty(_sigma_rule))" in parent
    assert "| where RuleCount >= 2\n" in parent


def test_ordered_parent_repeated_child_name_and_id_require_separate_times_but_one_type():
    child = correlation_document("child", "temporal_ordered", ["source-rule"])
    child["id"] = "22222222-2222-2222-2222-222222222222"
    parent = correlation_document("parent", "temporal_ordered", ["child", child["id"]])

    (query,) = convert_documents([source_document(), child, parent])

    assert query.count('| where _sigma_rule == "child"') == 2
    assert "| where _sigma_timestamp > _sigma_sequence_end and _sigma_timestamp <= _sigma_sequence_start + 5m" in query
    assert query.endswith("RuleCount = tolong(1)")


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

    assert query.startswith("let _sigma_child_0 = () {\nlet _sigma_child_0 = () {\n")
    assert query.count("\n};\n") == 2
    assert query.count("union (_sigma_child_0()") == 2
    assert '(Events\n| where Marker =~ "match"' in query
    assert '(NetworkEvents\n| where Marker =~ "direct"' in query
    assert 'todatetime(["ProcessTime"])' in query
    assert 'todatetime(["NetworkTime"])' in query
    assert 'tostring(["ProcessCase"])' in query
    assert 'tostring(["NetworkScope"])' in query
    assert 'tostring(["Case"])' in query
    assert 'tostring(["Tenant"])' in query
    assert 'pack_array(gettype(["EventCount"]), ["EventCount"])' in query
    assert query.count('todatetime(["WindowEnd"])') == 2
    assert "Wrong" not in query
    assert "ago(" not in query
    assert "| where _sigma_timestamp > _sigma_sequence_end and _sigma_timestamp <= _sigma_sequence_start + 4m" in query
    assert query.endswith('["Tenant"] = _sigma_group_0, RuleCount = tolong(2)')


def test_chain_bindings_do_not_shadow_known_tables_in_direct_or_descendant_sources():
    (query,) = convert_documents(multilevel_documents(), tables=("_sigma_child_0", "_sigma_types"))

    # The outer function must avoid the raw table two correlation levels below,
    # and the ordered parent's retained stream must avoid its direct table.
    assert query.startswith("let _sigma_child_0_1 = () {\nlet _sigma_child_0_1 = () {\n")
    assert '(_sigma_child_0\n| where Marker =~ "match"' in query
    assert '(_sigma_types\n| where Marker =~ "direct"' in query
    assert "let _sigma_child_0 =" not in query
    assert "let _sigma_types =" not in query
    assert "let _sigma_types_1 = union (_sigma_child_0_1()" in query


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
            assert query.endswith(metric)
    assert queries[-1].count("\n};\n") == 2


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
    assert queries[-2].endswith("EventCount")
    assert queries[-1].endswith("ValueCount")
    for parent in queries[-2:]:
        assert parent.count("let _sigma_child_0 = () {") == 1
        assert '_sigma_timestamp = todatetime(["WindowEnd"])' in parent
