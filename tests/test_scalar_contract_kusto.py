"""Scalar schema diagnostics and native identity through public conversion."""

import pytest
import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaConversionError
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.processing.pipeline import ProcessingPipeline
from tests.correlation_helpers import correlation_document, source_document


def convert_scalar(*, fields=None, groups=("User",), extra=(), transformations=()):
    pipeline = ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": "Events"},
                *transformations,
            ]
        }
    )
    if fields is not None:
        pipeline.vars["kusto_schema"] = BaseSchema(
            tables={"Events": TableSchema({name: FieldInfo(kind, "fixture") for name, kind in fields.items()})}
        )
    documents = [
        source_document(),
        correlation_document("scalar", "event_count", ["source-rule"], groups=groups, condition={"gte": 1}),
        *extra,
    ]
    return KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    )


def test_unknown_relevant_field_type_requires_partial_schema_declaration():
    with pytest.raises(SigmaConversionError, match="Timestamp.*Events.*kusto_schema"):
        convert_scalar()


def test_partial_schema_preserves_native_group_type_without_identity_restoration():
    (query,) = convert_scalar(fields={"Timestamp": "datetime", "User": "long"})
    assert 'GroupUser = ["User"]' in query
    assert "_sigma_identity" not in query
    assert "_sigma_group_lookup" not in query


@pytest.mark.parametrize("kind", ["dynamic", "array", "dictionary"])
def test_complex_group_fields_require_explicit_pipeline_normalization(kind):
    with pytest.raises(SigmaConversionError, match="User.*native scalar.*normalize.*kusto_schema"):
        convert_scalar(fields={"Timestamp": "datetime", "User": kind})


def test_child_scalar_group_type_survives_an_alias_and_document_order():
    parent = correlation_document(
        "parent",
        "event_count",
        ["scalar"],
        groups=("Actor",),
        aliases={"Actor": {"scalar": "User"}},
        condition={"gte": 1},
    )
    (query,) = convert_scalar(fields={"Timestamp": "datetime", "User": "guid"}, extra=(parent,))
    assert 'GroupActor = ["User"]' in query
    assert "_sigma_identity" not in query


def test_standard_mapping_resolves_final_physical_schema_without_full_table_declaration():
    (query,) = convert_scalar(
        fields={"Event Time": "datetime", "Account ID": "long"},
        transformations=({"type": "field_name_mapping", "mapping": {"Timestamp": "Event Time", "User": "Account ID"}},),
    )
    assert 'GroupUser = ["Account ID"]' in query


def test_incompatible_source_group_types_require_normalization():
    pipeline = ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": "Events"},
                {
                    "type": "set_state",
                    "key": "query_table",
                    "val": "NetworkEvents",
                    "rule_conditions": [{"type": "logsource", "product": "network"}],
                },
            ]
        }
    )
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            table: TableSchema({"Timestamp": FieldInfo("datetime", ""), "User": FieldInfo(kind, "")})
            for table, kind in [("Events", "long"), ("NetworkEvents", "string")]
        }
    )
    docs = [
        source_document(),
        source_document("other", product="network"),
        correlation_document("mixed", "event_count", ["source-rule", "other"], groups=("User",)),
    ]
    with pytest.raises(SigmaConversionError, match="User.*incompatible source types.*normalize"):
        KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
            SigmaCollection.from_yaml(yaml.safe_dump_all(docs))
        )


def test_scalar_distinct_count_has_no_recursive_identity_query():
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={"Events": TableSchema({"Timestamp": FieldInfo("datetime", ""), "User": FieldInfo("long", "")})}
    )
    docs = [
        source_document(),
        correlation_document("count", "value_count", ["source-rule"], groups=(), field="User", condition={"gte": 1}),
    ]
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(docs))
    )
    assert '_sigma_value = ["User"]' in query
    assert "_sigma_identity" not in query
    assert "assert(" not in query


@pytest.mark.parametrize(("kind", "metric"), [("value_sum", "ValueSum"), ("value_percentile", "ValuePercentile")])
def test_integer_numeric_metrics_are_returned_as_native_scalars(kind, metric):
    from tests.correlation_helpers import convert_documents

    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("numeric", kind, ["source-rule"], groups=(), field="Amount", condition={"gte": 0}),
        ]
    )
    assert f"{metric} = tolong(" in query


def test_numeric_strings_require_explicit_numeric_normalization():
    from tests.correlation_helpers import convert_documents, fixture_schema

    with pytest.raises(SigmaConversionError, match="Amount.*numeric.*normalize"):
        convert_documents(
            [
                source_document(),
                correlation_document("sum", "value_sum", ["source-rule"], field="Amount", condition={"gte": 1}),
            ],
            schema=fixture_schema(Amount="string"),
        )


@pytest.mark.parametrize("kind", ["string", "bool", "int", "long", "real", "decimal", "datetime", "timespan", "guid"])
def test_supported_native_group_types_use_direct_columns(kind):
    (query,) = convert_scalar(fields={"Timestamp": "datetime", "User": kind, "Unused": "dynamic"})
    assert 'GroupUser = ["User"]' in query
    assert "_sigma_identity" not in query


def test_final_physical_type_takes_precedence_after_explicit_normalization():
    (query,) = convert_scalar(
        fields={"Timestamp": "datetime", "User": "dynamic", "AccountID": "long"},
        transformations=({"type": "field_name_mapping", "mapping": {"User": "AccountID"}},),
    )
    assert 'GroupUser = ["AccountID"]' in query


@pytest.mark.parametrize("role", ["counted", "retained"])
def test_dynamic_counted_and_retained_fields_require_explicit_extraction(role):
    from tests.correlation_helpers import convert_documents, fixture_schema

    source = source_document()
    if role == "retained":
        source["fields"] = ["Payload"]
    correlation = correlation_document(
        "consumer",
        "value_count" if role == "counted" else "event_count",
        ["source-rule"],
        field="Payload",
        condition={"gte": 1},
    )
    with pytest.raises(SigmaConversionError, match="Payload.*native scalar.*extract.*normalize"):
        convert_documents([source, correlation], schema=fixture_schema(Payload="dynamic"))
