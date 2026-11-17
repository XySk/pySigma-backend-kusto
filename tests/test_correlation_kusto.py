import re
import runpy
from pathlib import Path

import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaConversionError, SigmaError
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, FieldMappings, TableSchema
from sigma.pipelines.microsoft365defender import microsoft_365_defender_pipeline as defender_pipeline
from sigma.processing.pipeline import ProcessingPipeline


def microsoft_365_defender_pipeline(*args, fixture_fields=(), **kwargs):
    """Declare additional fixture columns used beside built-in table fields."""
    from tests.correlation_helpers import fixture_schema

    pipeline = defender_pipeline(*args, **kwargs)
    from copy import deepcopy

    schema = deepcopy(pipeline.vars["kusto_schema"])
    pipeline.vars["kusto_schema"] = schema
    if table := kwargs.get("query_table"):
        schema.tables.setdefault(table, fixture_schema((table,)).tables[table])
    for table in schema.tables.values():
        for name, info in fixture_schema().tables["Events"].fields.items():
            table.fields.setdefault(name, info)
        table.fields.setdefault("TimestampField", FieldInfo("datetime", "Fixture timestamp"))
        for name in fixture_fields:
            table.fields.setdefault(name, FieldInfo("string", "Declared retained fixture column"))
    return pipeline


def summary_data_query(query):
    """Inspect summary computation separately from flat validation-only scans.

    Validation can repeat descendant summary computation to check its inputs;
    those copies do not contribute additional rows to the public result.
    """
    return re.split(r"\nlet _sigma_validation_check\w* = \(\) \{", query, maxsplit=1)[0]


def summary_projections(query):
    """Public summary columns, independently of surrounding validation bindings."""
    return [line for line in summary_data_query(query).splitlines() if line.startswith("| project WindowStart = ")]


def final_summary_projection(query):
    projections = summary_projections(query)
    return projections[-1] if projections else ""


def correlation_collection(correlation: str = "", source_conditions: str = "selection") -> SigmaCollection:
    return SigmaCollection.from_yaml(
        """
title: Source rule
name: source-rule
id: 11111111-1111-1111-1111-111111111111
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    CommandLine: suspicious.exe
  alternate:
    Image: suspicious.exe
  condition: """
        + source_conditions
        + """
---
title: Event count
"""
        + correlation
    )


def event_count_correlation(*, group_by=(), condition: str = "gte: 2", generate: bool = False) -> str:
    lines = ["correlation:", "  type: event_count", "  rules:", "    - source-rule"]
    if group_by:
        lines.extend(["  group-by:", *(f"    - {field}" for field in group_by)])
    lines.extend(["  timespan: 5m", "  condition:", f"    {condition}"])
    if generate:
        lines.append("  generate: true")
    return "\n".join(lines) + "\n"


def multi_rule_collection(
    *,
    source_conditions="selection",
    condition="gte: 2",
    group_by=(),
    aliases="",
    counted_field=None,
    other_source_conditions="selection",
):
    collection = (
        correlation_collection(event_count_correlation(), source_conditions=source_conditions).rules[0].to_dict()
    )
    other = {
        "title": "Other source rule",
        "name": "other-rule",
        "id": "22222222-2222-2222-2222-222222222222",
        "logsource": {"category": "network_connection", "product": "windows"},
        "detection": {"selection": {"DestinationPort": 443}, "condition": other_source_conditions},
    }
    correlation = (
        event_count_correlation(group_by=group_by, condition=condition)
        if counted_field is None
        else value_count_correlation(field=counted_field, group_by=group_by, condition=condition)
    ).replace("    - source-rule", "    - source-rule\n    - other-rule")
    return SigmaCollection.from_yaml(
        yaml.safe_dump(collection)
        + "---\n"
        + yaml.safe_dump(other)
        + "---\ntitle: Event count\n"
        + correlation
        + aliases
    )


def value_count_correlation(*, field="User", group_by=(), condition="gte: 2", generate=False):
    return (
        event_count_correlation(group_by=group_by, condition=condition, generate=generate)
        .replace("type: event_count", "type: value_count")
        .replace("  condition:\n", f"  condition:\n    field: {field}\n")
    )


def temporal_correlation(*, group_by=(), condition=None, generate=False):
    correlation = event_count_correlation(group_by=group_by, condition=condition or "gte: 1", generate=generate)
    correlation = correlation.replace("type: event_count", "type: temporal")
    return correlation if condition is not None else correlation.replace("  condition:\n    gte: 1\n", "")


def temporal_collection(*, condition=None, third_source=True, **kwargs):
    documents = [rule.to_dict() for rule in multi_rule_collection(**kwargs).rules]
    correlation = documents.pop()
    correlation["correlation"]["type"] = "temporal"
    correlation["correlation"].pop("condition")
    if condition is not None:
        correlation["correlation"]["condition"] = condition
    if third_source:
        documents.append(
            {
                **documents[0],
                "title": "Third source rule",
                "name": "third-rule",
                "id": "33333333-3333-3333-3333-333333333333",
            }
        )
        correlation["correlation"]["rules"].append("third-rule")
    return SigmaCollection.from_yaml(yaml.safe_dump_all([*documents, correlation]))


def ordered_temporal_correlation(**kwargs):
    return temporal_correlation(**kwargs).replace("type: temporal", "type: temporal_ordered")


def ordered_temporal_collection(**kwargs):
    collection = temporal_collection(**kwargs)
    documents = [rule.to_dict() for rule in collection.rules]
    documents[-1]["correlation"]["type"] = "temporal_ordered"
    if collection.rules[-1].custom_attributes["correlation"].get("condition") is None:
        documents[-1]["correlation"].pop("condition", None)
    return SigmaCollection.from_yaml(yaml.safe_dump_all(documents))


def test_temporal_defaults_to_all_source_types_in_exact_event_driven_windows():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(temporal_collection())

    assert 'SourceRule = "source-rule"' in query
    assert 'SourceRule = "other-rule"' in query
    assert 'SourceRule = "third-rule"' in query
    assert "LastSourceruleTime:datetime" in query
    assert "LastOtherruleTime:datetime" in query
    assert "LastThirdruleTime:datetime" in query
    assert "| where RuleCount >= 3\n" in query
    assert "join kind=" not in query
    assert "| project WindowStart = EventTime_1 - 5m, WindowEnd = EventTime_1, RuleCount" in query


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_temporal_explicit_conditions_compare_distinct_types_without_requiring_all(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(temporal_collection(condition={operator: 2}))

    assert f"| where RuleCount {query_operator} 2\n" in query
    assert "RuleCount >= 3" not in query
    assert "RuleCount = tolong(HasSourcerule) + tolong(HasOtherrule) + tolong(HasThirdrule)" in query
    assert "make_set(" not in query
    assert "dcount(" not in query


@pytest.mark.parametrize(("condition", "threshold"), [(None, 1), ({"gte": 2}, 2)])
def test_temporal_repeated_references_are_one_type_but_do_not_change_explicit_thresholds(condition, threshold):
    correlation = temporal_correlation(condition="gte: 2" if condition else None).replace(
        "    - source-rule", "    - source-rule\n    - 11111111-1111-1111-1111-111111111111"
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert summary_data_query(query).count('SourceRule = "source-rule"') == 2
    assert f"| where RuleCount >= {threshold}\n" in query


@pytest.mark.parametrize(
    ("correlation_rule", "metric"),
    [
        (event_count_correlation, "EventCount"),
        (value_count_correlation, "ValueCount"),
        (temporal_correlation, "RuleCount"),
        (ordered_temporal_correlation, "RuleCount = tolong(1)"),
    ],
)
def test_correlation_aliases_resolve_rule_identity_across_name_and_id_references(correlation_rule, metric):
    kwargs = {"field": "Actor"} if correlation_rule is value_count_correlation else {}
    correlation = correlation_rule(group_by=("Actor",), **kwargs).replace(
        "    - source-rule", "    - 11111111-1111-1111-1111-111111111111"
    )
    correlation += "  aliases:\n    Actor:\n      source-rule: User\n"
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    group = (
        "GroupActor"
        if correlation_rule
        in (event_count_correlation, value_count_correlation, temporal_correlation, ordered_temporal_correlation)
        else "_sigma_group_0"
    )
    assert f'{group} = ["AccountName"]' in query
    assert final_summary_projection(query).endswith(f'["Actor"] = {group}, {metric}')
    if correlation_rule is value_count_correlation:
        assert 'CountedActor = ["AccountName"]' in query


@pytest.mark.parametrize("ordered", [False, True])
def test_temporal_maps_group_aliases_and_timestamps_for_all_searches_across_tables(ordered):
    collection = (ordered_temporal_collection if ordered else temporal_collection)(
        third_source=False,
        source_conditions="[selection, alternate]",
        other_source_conditions=["selection", "selection"],
        group_by=("Host", "User"),
        aliases="  aliases:\n    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n",
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {"EventTime": "ProcessTime", "Computer": "ProcessHost", "User": "ProcessUser"},
            "DeviceNetworkEvents": {
                "EventTime": "NetworkTime",
                "SourceComputer": "NetworkHost",
                "User": "NetworkUser",
            },
        }
    )

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime").convert(collection)

    assert summary_data_query(query).count("(DeviceProcessEvents\n| where ") == 2
    assert summary_data_query(query).count("(DeviceNetworkEvents\n| where ") == 2
    host, user = "GroupHost", "GroupUser"
    for prefix in ("Process", "Network"):
        assert query.count(f'["{prefix}Time"], {host} = ["{prefix}Host"], {user} = ["{prefix}User"]') == 2
    timestamp = "EventTime_1"
    assert query.count(f"| where isnotnull({timestamp}) and isnotempty({host}) and isnotempty({user})") == 4
    if ordered:
        assert "| scan declare (" in query
        assert "GroupHost != prev(GroupHost) or GroupUser != prev(GroupUser)" in query
    else:
        assert "join kind=" not in query
        assert final_summary_projection(query).endswith('["Host"] = GroupHost, ["User"] = GroupUser, RuleCount')


@pytest.mark.parametrize("named", [True, False])
@pytest.mark.parametrize("ordered", [False, True])
def test_temporal_preserves_colliding_source_columns_escaped_identity_and_standalone_searches(named, ordered):
    collection = correlation_collection(
        (ordered_temporal_correlation if ordered else temporal_correlation)(generate=True),
        source_conditions="[selection, alternate]",
    )
    documents = [rule.to_dict() for rule in collection.rules]
    source = documents[0]
    source["fields"] = [
        "_sigma_timestamp",
        "_sigma_rule",
        "_sigma_rule_1",
        "_sigma_types",
        "_sigma_sequence_start",
        "_sigma_sequence_end",
        "_sigma_sequence_0",
    ]
    if named:
        source["name"] = 'source"rule'
        identity = 'source\\"rule'
    else:
        source.pop("name")
        identity = source["id"]
    documents[-1]["correlation"]["rules"] = [source["id"]]
    documents[-1]["correlation"]["generate"] = True
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(fixture_fields=source["fields"]),
        correlation_timestamp_field="Timestamp",
    )

    *standalone, query = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all(documents)))

    query = summary_data_query(query)

    assert len(standalone) == 2
    assert standalone[0] == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert query.count('EventTime_1 = ["Timestamp"]') == 2
    assert len(re.findall(r'(?<![A-Za-z_])SourceRule = "' + re.escape(identity) + '"', query)) == 2
    if ordered:
        assert "| summarize by EventTime_1" in query
        assert "WindowEnd = EventTime_1, RuleCount = tolong(1)" in query
    else:
        assert "| scan declare (Last" in query
        assert "RuleCount = tolong(Has" in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "RuleCount"])
@pytest.mark.parametrize("correlation", [temporal_correlation, ordered_temporal_correlation])
def test_temporal_rejects_group_names_that_collide_with_results(group, correlation):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="grouping fields conflict with result columns"):
        backend.convert(correlation_collection(correlation(group_by=(group,))))


def test_ordered_temporal_requires_the_declared_full_sequence_with_strict_times_and_total_span():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(ordered_temporal_collection())

    assert "| join " not in query
    assert "| scan declare (" in query
    assert 'countif(SourceRule == "source-rule") > 0' in query
    assert 'countif(SourceRule == "other-rule") > 0' in query
    assert 'countif(SourceRule == "third-rule") > 0' in query
    assert ">= EventTime_1 - 5m" in query
    assert "| where CompleteNow" in query
    assert final_summary_projection(query).endswith("WindowEnd = EventTime_1, RuleCount = tolong(3)")


@pytest.mark.parametrize(
    "condition", [{"gte": 2}, {"gt": 1}, {"eq": 2}, {"neq": 2}, {"lt": 3}, {"lte": 3}, {"gte": 4}, {"gt": 3}]
)
def test_ordered_temporal_rejects_conditions_that_do_not_require_the_complete_sequence(condition):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(
        SigmaError,
        match="temporal_ordered requires the full declared sequence.*omit the condition.*gte: 3.*eq: 3.*gt: 2",
    ):
        backend.convert(ordered_temporal_collection(condition=condition))


@pytest.mark.parametrize("condition", [None, {"gte": 3}, {"eq": 3}, {"gt": 2}])
def test_ordered_temporal_accepts_equivalent_full_sequence_conditions(condition):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(ordered_temporal_collection(condition=condition))

    assert "| scan declare (" in query
    assert final_summary_projection(query).endswith("RuleCount = tolong(3)")


@pytest.mark.parametrize("condition", [None, "gte: 1", "eq: 1", "gt: 0", "neq: 0"])
def test_ordered_temporal_repeated_list_positions_require_separate_later_matches_of_one_type(condition):
    correlation = ordered_temporal_correlation(condition=condition).replace(
        "    - source-rule", "    - source-rule\n    - 11111111-1111-1111-1111-111111111111"
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert query.count('countif(SourceRule == "source-rule") > 0') == 1
    assert "| where CompleteNow" in query
    assert final_summary_projection(query).endswith("RuleCount = tolong(1)")


def test_value_count_rejects_unnormalized_dynamic_identity():
    from tests.correlation_helpers import fixture_schema

    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    pipeline.vars["kusto_schema"] = fixture_schema(Value="dynamic")
    with pytest.raises(SigmaConversionError, match="Value.*native scalar.*normalize"):
        KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
            correlation_collection(value_count_correlation(field="Value"))
        )


def test_value_count_retains_values_for_exact_window_wide_counts_and_missing_value_anchors():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(value_count_correlation(condition="eq: 0")))

    assert 'CountedUser = ["AccountName"]' in query
    assert "| where isnotnull(EventTime_1));" in query
    assert "| where isnotempty(CountedUser)" in query
    assert "ValueCountSources | summarize by EventTime_1" in query
    assert "ValueCount = row_cumsum(CountedUserDelta)" in query
    assert "| where ValueCount == 0" in query
    assert "join kind=inner" not in query
    assert final_summary_projection(query).endswith("WindowEnd = ValueBoundaryTime, ValueCount")


@pytest.mark.parametrize(
    ("operator", "query_operator", "threshold"),
    [("gt", ">", 2), ("gte", ">=", 2), ("lt", "<", 2), ("lte", "<=", 2), ("eq", "==", 0), ("neq", "!=", 2)],
)
def test_multi_field_value_count_requires_each_exact_count_and_retains_missing_value_anchors(
    operator, query_operator, threshold
):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(
        correlation_collection(
            value_count_correlation(
                field="[User, Target]", group_by=("DeviceName",), condition=f"{operator}: {threshold}"
            )
        )
    )

    assert 'CountedUser = ["AccountName"], CountedTarget = ["Target"]' in query
    assert "isnotnull(EventTime_1) and isnotempty(GroupDeviceName)" in query
    assert "| where isnotempty(CountedUser)" in query and "| where isnotempty(CountedTarget)" in query
    assert '["ValueCount_User"] = row_cumsum(CountedUserDelta' in query
    assert '["ValueCount_Target"] = row_cumsum(CountedTargetDelta' in query
    assert (
        f'| where ["ValueCount_User"] {query_operator} {threshold} and ["ValueCount_Target"] {query_operator} {threshold}'
        in query
    )
    assert final_summary_projection(query).endswith(
        '["DeviceName"] = GroupDeviceName, ["ValueCount_User"], ["ValueCount_Target"]'
    )


def test_multi_field_value_count_maps_each_counted_alias_for_all_source_searches():
    collection = multi_rule_collection(
        source_conditions="[selection, alternate]",
        other_source_conditions=["selection", "selection"],
        counted_field="[Actor, Target]",
        group_by=("Host",),
        aliases=(
            "  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n"
            "    Target:\n      source-rule: File\n      other-rule: Destination\n"
            "    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n"
        ),
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {
                "EventTime": "ProcessTime",
                "User": "ProcessUser",
                "File": "ProcessTarget",
                "Computer": "ProcessHost",
            },
            "DeviceNetworkEvents": {
                "EventTime": "NetworkTime",
                "SourceUser": "NetworkUser",
                "Destination": "NetworkTarget",
                "SourceComputer": "NetworkHost",
            },
        }
    )

    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime").convert(collection)

    query = summary_data_query(query)

    assert summary_data_query(query).count("(DeviceProcessEvents\n| where ") == 2
    assert summary_data_query(query).count("(DeviceNetworkEvents\n| where ") == 2
    assert summary_data_query(query).count('CountedActor = ["ProcessUser"], CountedTarget = ["ProcessTarget"]') == 2
    assert summary_data_query(query).count('CountedActor = ["NetworkUser"], CountedTarget = ["NetworkTarget"]') == 2
    assert summary_data_query(query).count('["ProcessTime"], GroupHost = ["ProcessHost"]') == 2
    assert summary_data_query(query).count('["NetworkTime"], GroupHost = ["NetworkHost"]') == 2
    assert summary_data_query(query).count('["ValueCount_Actor"] = row_cumsum(') == 1
    assert final_summary_projection(query).endswith('["Host"] = GroupHost, ["ValueCount_Actor"], ["ValueCount_Target"]')


def test_multi_field_value_count_quotes_metrics_and_preserves_colliding_source_columns_and_generation():
    counted = yaml.safe_dump(['Actor\'s "Name"', "_sigma_value_0"], default_flow_style=True).strip()
    collection = correlation_collection(value_count_correlation(field=counted, generate=True))
    collection.rules[0].fields = ["CountedActorsName", "Counted_sigma_value_0", "ValueCountSources"]
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(
            fixture_fields=[*collection.rules[0].fields, 'Actor\'s "Name"', "_sigma_value_0"]
        ),
        correlation_timestamp_field="Timestamp",
    )

    standalone, query = backend.convert(collection)

    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert "let ValueCountSources_1 = materialize(DeviceProcessEvents\n" in query
    assert 'CountedActorsName_1 = ["Actor\'s \\"Name\\""]' in query
    assert 'Counted_sigma_value_0_1 = ["_sigma_value_0"]' in query
    assert "| project EventTime" in query
    assert '["ValueCount_Actor\'s \\"Name\\""] = row_cumsum(CountedActorsName_1Delta' in query
    assert '["ValueCount_Actor\'s \\"Name\\""], ["ValueCount__sigma_value_0"]' in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "ValueCount_User", "ValueCount_Target"])
def test_multi_field_value_count_rejects_group_names_that_collide_with_per_field_metrics(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count grouping fields conflict with result columns"):
        backend.convert(correlation_collection(value_count_correlation(field="[User, Target]", group_by=(group,))))


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_value_count_applies_each_comparison_after_exact_aggregation(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(
        correlation_collection(value_count_correlation(group_by=("DeviceName",), condition=f"{operator}: 2"))
    )

    assert "ValueCount = row_cumsum(CountedUserDelta" in query
    assert f"| where ValueCount {query_operator} 2" in query
    assert final_summary_projection(query).endswith('["DeviceName"] = GroupDeviceName, ValueCount')


def test_value_count_maps_counted_and_grouping_aliases_per_source_table_and_retains_all_searches():
    collection = multi_rule_collection(
        source_conditions="[selection, alternate]",
        counted_field="Actor",
        group_by=("Host", "DeviceName"),
        aliases=(
            "  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n"
            "    Host:\n      source-rule: Computer\n      other-rule: SourceComputer\n"
        ),
    )
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={
            "DeviceProcessEvents": {"EventTime": "ProcessTime", "User": "ProcessUser", "Computer": "ProcessHost"}
        }
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    def change_other_mapping(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            backend.last_processing_pipeline.vars["kusto_field_mappings"] = FieldMappings(
                table_mappings={
                    "DeviceNetworkEvents": {
                        "EventTime": "NetworkTime",
                        "SourceUser": "NetworkUser",
                        "SourceComputer": "NetworkHost",
                    }
                }
            )
        return result

    (query,) = backend.convert(collection, callback=change_other_mapping)

    query = summary_data_query(query)

    assert summary_data_query(query).count("(DeviceProcessEvents\n| where ") == 2
    assert summary_data_query(query).count("(DeviceNetworkEvents\n| where ") == 1
    assert summary_data_query(query).count('CountedActor = ["ProcessUser"]') == 2
    assert 'CountedActor = ["NetworkUser"]' in query
    assert '["ProcessTime"], GroupHost = ["ProcessHost"]' in query
    assert '["NetworkTime"], GroupHost = ["NetworkHost"]' in query
    assert "| where isnotnull(EventTime_1) and isnotempty(GroupHost) and isnotempty(GroupDeviceName)" in query
    assert summary_data_query(query).count("ValueCount = row_cumsum(") == 1
    assert final_summary_projection(query).endswith(
        '["Host"] = GroupHost, ["DeviceName"] = GroupDeviceName, ValueCount'
    )


def test_single_source_value_count_aliases_quoted_counted_fields_without_overwriting_source_columns():
    correlation = value_count_correlation(field="Actor") + "  aliases:\n    Actor:\n      source-rule: User\n"
    collection = correlation_collection(correlation, source_conditions="[selection, alternate]")
    collection.rules[0].fields = [
        "ValueCountSources",
        "CountedActor",
        "EventTime",
        "_sigma_window_end",
        "_sigma_bin",
    ]
    pipeline = microsoft_365_defender_pipeline(fixture_fields=collection.rules[0].fields)
    pipeline.vars["kusto_field_mappings"] = FieldMappings(generic_mappings={"User": 'Actor\'s "Name"'})
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp")

    (query,) = backend.convert(collection)

    query = summary_data_query(query)

    assert "let ValueCountSources_1 = materialize(union " in query
    assert summary_data_query(query).count('CountedActor_1 = ["Actor\'s \\"Name\\""]') == 2
    assert '| project EventTime_1 = ["Timestamp"]' in query
    assert "ValueCount = row_cumsum(CountedActor_1Delta" in query
    assert "join kind=inner" not in summary_data_query(query).split("};", 1)[0]


@pytest.mark.parametrize("field", ["[]", "''", "42", "[User, '']", "[User, 42]"])
def test_value_count_rejects_invalid_counted_fields(field):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count requires nonempty counted field names"):
        backend.convert(correlation_collection(value_count_correlation(field=field)))


def test_value_count_rejects_duplicate_counted_fields_with_ambiguous_result_metrics():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count counted field names must be unique"):
        backend.convert(correlation_collection(value_count_correlation(field="[User, User]")))


def test_value_count_accepts_a_single_field_list_and_preserves_source_generation():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    standalone, query = backend.convert(correlation_collection(value_count_correlation(field="[User]", generate=True)))

    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert 'CountedUser = ["AccountName"]' in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "ValueCount"])
def test_value_count_rejects_group_names_that_collide_with_result_columns(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="value_count grouping fields conflict with result columns"):
        backend.convert(correlation_collection(value_count_correlation(group_by=(group,))))


def test_single_source_event_count_combines_searches_and_maps_aliases():
    correlation = event_count_correlation(group_by=("Actor",)) + ("  aliases:\n    Actor:\n      source-rule: User\n")
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(correlation_collection(correlation, source_conditions="[selection, alternate]"))

    assert summary_data_query(query).count("(DeviceProcessEvents\n| where ") == 2
    assert summary_data_query(query).count('GroupActor = ["AccountName"]') == 2
    assert summary_data_query(query).count("| extend EventCount = row_cumsum(") == 1


def test_multi_rule_event_count_combines_source_tables_before_counting():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(multi_rule_collection(group_by=("User",)))

    assert 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"' in query
    assert "DeviceNetworkEvents\n| where RemotePort == 443" in query
    assert 'GroupUser = ["AccountName"]' in query
    assert 'GroupUser = ["InitiatingProcessAccountName"]' in query
    assert "| extend EventCount = row_cumsum(SourceMatchDelta, GroupUser != prev(GroupUser))\n" in query
    assert final_summary_projection(query).endswith('["User"] = GroupUser, EventCount')


def test_event_count_uses_exact_inclusive_windows_at_distinct_event_timestamps():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(correlation_collection(event_count_correlation(group_by=("User", "DeviceName"))))

    assert "CountBoundaryTime = EventTime_1 + 5m" in query
    assert "| where EventCount >= 2" in query
    assert final_summary_projection(query).endswith(
        '["User"] = GroupUser, ["DeviceName"] = GroupDeviceName, EventCount'
    )


@pytest.mark.parametrize(
    ("invalid_state", "error"),
    [
        ("unresolved", "source rule reference is unresolved"),
        ("no-queries", "convertible source-rule filter"),
        ("no-states", "convertible source-rule filter"),
        ("unpaired", "source query and conversion state counts do not match"),
        ("no-table", "mapped query table"),
        ("different-tables", "same mapped query table"),
    ],
)
@pytest.mark.parametrize("counted_field", [None, "User", "temporal", "temporal_ordered"])
def test_count_correlations_reject_invalid_source_conversion_state(invalid_state, error, counted_field):
    collection = (
        (ordered_temporal_collection if counted_field == "temporal_ordered" else temporal_collection)(
            third_source=False, source_conditions="[selection, alternate]"
        )
        if counted_field in ("temporal", "temporal_ordered")
        else multi_rule_collection(source_conditions="[selection, alternate]", counted_field=counted_field)
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="Timestamp",
    )

    def invalidate_first_source(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            source = collection.rules[0]
            if invalid_state == "unresolved":
                collection.rules[-1].rules[0].rule = None
            elif invalid_state == "no-queries":
                source.set_conversion_result([])
            elif invalid_state == "no-states":
                source.set_conversion_states([])
            elif invalid_state == "unpaired":
                source.set_conversion_states(source.get_conversion_states()[:1])
            elif invalid_state == "no-table":
                source.get_conversion_states()[0].processing_state.pop("query_table")
            elif invalid_state == "different-tables":
                source.get_conversion_states()[1].processing_state["query_table"] = "DeviceNetworkEvents"
        return result

    with pytest.raises(SigmaError, match=error):
        backend.convert(collection, callback=invalidate_first_source)


def test_multi_rule_event_count_maps_aliases_and_timestamp_from_each_source_pipeline():
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        table_mappings={"DeviceProcessEvents": {"EventTime": "ProcessTime", "User": "ProcessUser"}}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    def change_mapping_for_other_source(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            backend.last_processing_pipeline.vars["kusto_field_mappings"] = FieldMappings(
                table_mappings={"DeviceNetworkEvents": {"EventTime": "NetworkTime", "SourceUser": "NetworkUser"}}
            )
        return result

    collection = multi_rule_collection(
        group_by=("Actor",),
        aliases="  aliases:\n    Actor:\n      source-rule: User\n      other-rule: SourceUser\n",
    )
    (query,) = backend.convert(collection, callback=change_mapping_for_other_source)

    assert '["ProcessTime"], GroupActor = ["ProcessUser"]' in query
    assert '["NetworkTime"], GroupActor = ["NetworkUser"]' in query


def test_multi_rule_event_count_quotes_rule_identity_and_special_mapped_fields():
    collection = multi_rule_collection(group_by=("User",))
    collection.rules[-1].rules[0].reference = str(collection.rules[0].id)
    collection.rules[0].name = 'source"\\\n*'
    collection.rules[-1].rules[1].reference = str(collection.rules[1].id)
    collection.rules[1].name = None
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        generic_mappings={"EventTime": "Event Time", "User": "Actor's Name"}
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    (query,) = backend.convert(collection)

    assert '["Event Time"]' in query
    assert '["Actor\'s Name"]' in query


def test_multi_rule_event_count_preserves_source_columns_that_overlap_normalized_names():
    collection = multi_rule_collection(group_by=("User",))
    collection.rules[0].fields = ["_sigma_timestamp", "_sigma_timestamp_1", "_sigma_rule_1"]
    pipeline = microsoft_365_defender_pipeline()
    pipeline.vars["kusto_field_mappings"] = FieldMappings(
        generic_mappings={"EventTime": "_sigma_timestamp_2", "User": "_sigma_group_0", "Unused": "_sigma_rule"}
    )
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            table: TableSchema(
                fields={
                    "_sigma_rule_2": FieldInfo("string", "Original data"),
                    "_sigma_timestamp_2": FieldInfo("datetime", "Mapped timestamp"),
                    "_sigma_group_0": FieldInfo("string", "Mapped group"),
                    **{field: FieldInfo("string", "Retained column") for field in collection.rules[0].fields},
                }
            )
            for table in ("DeviceProcessEvents", "DeviceNetworkEvents")
        }
    )
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="EventTime")

    (query,) = backend.convert(collection)

    assert '| project EventTime_1 = ["_sigma_timestamp_2"], GroupUser = ["_sigma_group_0"]' in query
    assert "CountBoundaryTime = EventTime_1 + 5m" in query
    assert "EventCount = row_cumsum(SourceMatchDelta, GroupUser != prev(GroupUser))" in query
    assert "| project " not in query.split("| project EventTime_1")[0]


def test_event_count_preserves_columns_that_overlap_sliding_window_names():
    collection = correlation_collection(event_count_correlation(group_by=("User",)))
    collection.rules[0].fields = ["_sigma_weight", "_sigma_window_end", "_sigma_bin", "_sigma_weights"]
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(fixture_fields=collection.rules[0].fields),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(collection)

    assert "let SourceMatchArrivals = DeviceProcessEvents\n" in query
    assert "| summarize SourceMatchCount = count() by EventTime_1, GroupUser;" in query
    assert "EventCount = row_cumsum(SourceMatchDelta, GroupUser != prev(GroupUser))" in query
    for field in collection.rules[0].fields:
        assert f'Context{field} = ["{field}"]' in query
        assert f'["{field}"] = coalesce(Displayed{field}, dynamic([]))' in query


def test_event_count_quotes_requested_group_names_in_result_summary():
    correlation = event_count_correlation(group_by=("'Actor\" Name'",)) + (
        '  aliases:\n    Actor" Name:\n      source-rule: User\n'
    )
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(correlation_collection(correlation))

    assert 'GroupActorName = ["AccountName"]' in query
    assert final_summary_projection(query).endswith('["Actor\\" Name"] = GroupActorName, EventCount')


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_multi_rule_event_count_retains_every_search_and_duplicate_rule_matches(operator, query_operator):
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "CustomEvents"}]}
    )
    from tests.correlation_helpers import fixture_schema

    pipeline.vars["kusto_schema"] = fixture_schema(("CustomEvents",))
    backend = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp")
    collection = multi_rule_collection(source_conditions="[selection, alternate]", condition=f"{operator}: 3")
    collection.rules[1].detection = collection.rules[0].detection

    (query,) = backend.convert(collection)

    # The same event can satisfy both named rules, and both searches from each
    # rule must contribute rows to the one combined count.
    assert summary_data_query(query).count('(CustomEvents\n| where CommandLine =~ "suspicious.exe"') == 2
    assert summary_data_query(query).count('(CustomEvents\n| where Image =~ "suspicious.exe"') == 2
    assert "| summarize SourceMatchCount = count() by EventTime_1;" in query
    assert "| extend EventCount = row_cumsum(SourceMatchDelta)\n" in query
    assert f"| where EventCount {query_operator} 3\n" in query


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_event_count_correlation_uses_source_pipeline_and_comparison(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    result = backend.convert(
        correlation_collection(event_count_correlation(group_by=("User",), condition=f"{operator}: 2"))
    )

    (query,) = result
    assert 'let SourceMatchArrivals = DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in query
    assert 'EventTime_1 = ["TimestampField"], GroupUser = ["AccountName"]' in query
    assert f"| where EventCount {query_operator} 2\n" in query


def test_event_count_correlation_preserves_multiple_source_queries():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    result = backend.convert(
        correlation_collection(
            event_count_correlation(),
            source_conditions="[selection, alternate]",
        )
    )

    (query,) = result
    assert '(DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in query
    assert '(DeviceProcessEvents\n| where FolderPath =~ "suspicious.exe"\n' in query
    assert summary_data_query(query).count("| extend EventCount = row_cumsum(") == 1


def test_event_count_correlation_rejects_unpaired_source_query_states():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    def skip_second_query(rule, output_format, index, condition, result):
        return None if index == 1 else result

    with pytest.raises(SigmaError, match="source query and conversion state counts do not match"):
        backend.convert(
            correlation_collection(
                event_count_correlation(),
                source_conditions="[selection, alternate]",
            ),
            callback=skip_second_query,
        )


@pytest.mark.parametrize(
    "correlation",
    [event_count_correlation, value_count_correlation, temporal_correlation, ordered_temporal_correlation],
)
def test_count_correlations_require_timestamp_option(correlation):
    backend = KustoBackend(processing_pipeline=microsoft_365_defender_pipeline())

    with pytest.raises(SigmaError, match="correlation_timestamp_field"):
        backend.convert(correlation_collection(correlation(group_by=("DeviceName",))))


@pytest.mark.parametrize("span", ["0m", "-5m"])
@pytest.mark.parametrize(
    "correlation_rule",
    [event_count_correlation, value_count_correlation, temporal_correlation, ordered_temporal_correlation],
)
def test_count_correlations_require_a_positive_timespan(span, correlation_rule):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )
    correlation = correlation_rule().replace("5m", span)

    with pytest.raises(SigmaError, match="positive timespan|greater than zero|is invalid"):
        backend.convert(correlation_collection(correlation))


@pytest.mark.parametrize(("span", "seconds"), [("2w", 1209600), ("1M", 2629746), ("1y", 31556952)])
def test_event_count_renders_extended_sigma_duration_units_as_kusto_seconds(span, seconds):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )
    correlation = event_count_correlation().replace("5m", span)

    (query,) = backend.convert(correlation_collection(correlation))

    assert f"CountBoundaryTime = EventTime_1 + {seconds}s" in query
    assert f"WindowStart = CountBoundaryTime - {seconds}s" in query


@pytest.mark.parametrize("group", ["WindowStart", "WindowEnd", "EventCount"])
def test_event_count_rejects_group_names_that_collide_with_result_metrics(group):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match="grouping fields conflict with result columns"):
        backend.convert(correlation_collection(event_count_correlation(group_by=(group,))))


def chained_event_count_collection(
    *, child_groups=(), parent_groups=(), child_generate=False, parent_generate=False, parent_condition="gte: 2"
):
    source = correlation_collection(event_count_correlation()).rules[0].to_dict()
    child = event_count_correlation(group_by=child_groups, condition="gte: 10", generate=child_generate)
    child = "title: Child\nname: child-correlation\n" + child
    parent = event_count_correlation(group_by=parent_groups, generate=parent_generate, condition=parent_condition)
    parent = "title: Parent\n" + parent.replace("source-rule", "child-correlation").replace(
        "timespan: 5m", "timespan: 2m"
    )
    return SigmaCollection.from_yaml(yaml.safe_dump(source) + "---\n" + child + "---\n" + parent)


def test_event_count_parent_counts_child_summaries_at_window_end_without_expanding_their_counts():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(chained_event_count_collection())

    assert "let ChildCorrelation = () {\nDeviceProcessEvents\n| where " in summary_data_query(query)
    assert re.search(r"prev\(EventTime(?:_\d+)?, 9\)", query)
    assert "| where EventCount >= 10\n" not in query
    assert "let SourceMatchArrivals = ChildCorrelation()\n" in summary_data_query(query)
    assert 'EventTime = ["WindowEnd"]' in query
    assert summary_data_query(query).count("| summarize SourceMatchCount = count() by EventTime") == 1
    assert "CountBoundaryTime = EventTime + 2m" in query
    assert final_summary_projection(query).endswith(
        "| project WindowStart = CountBoundaryTime - 2m, WindowEnd = CountBoundaryTime, EventCount"
    )


@pytest.mark.parametrize("field", ["User", "CommandLine", "Timestamp"])
def test_event_count_parent_rejects_fields_discarded_by_child_aggregation(field):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    with pytest.raises(SigmaError, match=f"child-correlation.*does not return required field.*{field}"):
        backend.convert(chained_event_count_collection(parent_groups=(field,)))


def test_event_count_parent_resolves_child_group_and_metric_aliases_without_remapping_result_fields():
    collection = chained_event_count_collection(child_groups=("User",), parent_groups=("Actor", "ChildCount"))
    documents = [rule.to_dict() for rule in collection.rules]
    documents[1]["id"] = "22222222-2222-2222-2222-222222222222"
    documents[2]["correlation"]["rules"] = [documents[1]["id"]]
    documents[2]["correlation"]["aliases"] = {
        "Actor": {"child-correlation": "User"},
        "ChildCount": {"child-correlation": "EventCount"},
    }
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all(documents)))

    assert 'GroupUser = ["AccountName"]' in query
    assert 'GroupActor = ["User"]' in query
    assert 'GroupChildCount = ["EventCount"]' in query
    assert final_summary_projection(query).endswith(
        '["Actor"] = GroupActor, ["ChildCount"] = GroupChildCount, EventCount'
    )


def test_event_count_parent_combines_direct_searches_with_child_groups_and_preserves_internal_name_collisions():
    collection = chained_event_count_collection(
        child_groups=("User", "_sigma_timestamp", "_sigma_child_0"), parent_groups=("Actor",)
    )
    source, child, parent = [rule.to_dict() for rule in collection.rules]
    source["detection"]["condition"] = ["selection", "alternate"]
    direct = multi_rule_collection(other_source_conditions=["selection", "selection"]).rules[1].to_dict()
    parent["correlation"]["rules"].append("other-rule")
    parent["correlation"]["aliases"] = {"Actor": {"child-correlation": "User", "other-rule": "User"}}
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(fixture_fields=child["correlation"]["group-by"]),
        correlation_timestamp_field="Timestamp",
    )

    (query,) = backend.convert(SigmaCollection.from_yaml(yaml.safe_dump_all([parent, child, source, direct])))

    assert "let ChildCorrelation = () {\n" in query
    assert summary_data_query(query).count("(DeviceProcessEvents\n| where ") == 2
    assert summary_data_query(query).count("(DeviceNetworkEvents\n| where ") == 2
    assert 'EventTime_1 = ["WindowEnd"], GroupActor = ["User"]' in query
    assert 'EventTime_1 = ["Timestamp"], GroupActor = ["InitiatingProcessAccountName"]' in query
    assert final_summary_projection(query).endswith('["Actor"] = GroupActor, EventCount')


@pytest.mark.parametrize("missing", ["kusto_result_fields", "kusto_result_timestamp"])
def test_event_count_chain_rejects_missing_child_result_schema_or_time_metadata(missing):
    documents = [rule.to_dict() for rule in chained_event_count_collection().rules]
    documents.insert(2, multi_rule_collection().rules[1].to_dict())
    documents[-1]["correlation"]["rules"].append("other-rule")
    collection = SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    def remove_child_metadata(rule, output_format, index, condition, result):
        if rule.name == "other-rule":
            child = next(rule for rule in collection.rules if rule.name == "child-correlation")
            child.get_conversion_states()[0].processing_state.pop(missing)
        return result

    with pytest.raises(SigmaError, match="child result schema and timestamp metadata"):
        backend.convert(collection, callback=remove_child_metadata)


@pytest.mark.parametrize(
    ("child_generate", "parent_generate", "output_count"),
    [(False, False, 1), (False, True, 2), (True, False, 2), (True, True, 3)],
)
def test_event_count_chain_honors_generation_of_child_and_detection_queries(
    child_generate, parent_generate, output_count
):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    queries = backend.convert(
        chained_event_count_collection(child_generate=child_generate, parent_generate=parent_generate)
    )

    assert len(queries) == output_count
    if child_generate:
        assert queries[0] == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    if parent_generate:
        assert "| where EventCount >= 10\n" in queries[-2]
        assert "_sigma_child_0" not in queries[-2]
    child_binding = "ChildCorrelation"
    assert f"{child_binding}()" in queries[-1]


@pytest.mark.parametrize(
    ("operator", "query_operator"),
    [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<="), ("eq", "=="), ("neq", "!=")],
)
def test_event_count_chain_applies_parent_comparisons_after_counting_child_summaries(operator, query_operator):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(), correlation_timestamp_field="Timestamp"
    )

    (query,) = backend.convert(chained_event_count_collection(parent_condition=f"{operator}: 2"))

    assert f"| where CountBoundaryOrder == 0\n| where EventCount {query_operator} 2\n" in query


@pytest.mark.parametrize(
    "correlation",
    [
        """correlation:
  type: value_percentile
  rules:
    - source-rule
  timespan: 5m
  condition:
    percentile: -1
    field: User
    gte: 2
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
    - other-rule
  timespan: 5m
  condition:
    gte: 2
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
  group-by:
    - User
  aliases:
    User: UserName
  timespan: 5m
  condition:
    gte: 2
""",
        """correlation:
  type: event_count
  rules:
    - source-rule
  timespan: 5m
  condition:
    - gte: 2
    - lte: 5
""",
    ],
)
def test_unsupported_correlation_inputs_fail_clearly(correlation):
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )

    with pytest.raises(SigmaError):
        backend.convert(correlation_collection(correlation))


def test_event_count_range_condition_error_explains_single_operator_limit():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )
    with pytest.raises(SigmaError, match="exactly one condition item"):
        backend.convert(
            correlation_collection(
                """correlation:
  type: event_count
  rules:
    - source-rule
  timespan: 5m
  condition:
    gte: 2
    lte: 5
"""
            )
        )


def test_generate_controls_standalone_source_query():
    backend = KustoBackend(
        processing_pipeline=microsoft_365_defender_pipeline(),
        correlation_timestamp_field="TimestampField",
    )
    collection = correlation_collection(event_count_correlation(group_by=("User",), generate=True))

    standalone, correlation = backend.convert(collection)
    assert standalone == 'DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"'
    assert (
        'let SourceMatchArrivals = DeviceProcessEvents\n| where ProcessCommandLine =~ "suspicious.exe"\n' in correlation
    )
    assert "| where EventCount >= 2\n" in correlation


def test_prepared_kql_fixtures_use_current_public_conversion_output():
    fixture_directory = Path(__file__).parent / "fixtures" / "correlation"
    prepared = runpy.run_path(str(fixture_directory / "generate.py"))["fixtures"]()

    for filename, content in prepared.items():
        assert (fixture_directory / filename).read_text() == content
