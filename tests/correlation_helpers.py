"""Shared Sigma inputs and public conversion for tests and prepared fixtures."""

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.processing.pipeline import ProcessingPipeline


def source_document(name="source-rule", marker="match", *, product="fixture"):
    return {
        "title": name,
        "name": name,
        "logsource": {"product": product},
        "detection": {"selection": {"Marker": marker}, "condition": "selection"},
    }


def correlation_document(name, kind, rules, *, groups=("Host",), field=None, condition=None, span="5m", **kwargs):
    correlation = {"type": kind, "rules": rules, "timespan": span, "group-by": list(groups), **kwargs}
    if condition is not None or kind in ("event_count", "value_count", "value_sum", "value_percentile"):
        correlation["condition"] = dict(condition or {"gte": 2})
    if kind == "value_count":
        correlation["condition"]["field"] = field or "User"
    elif kind in ("value_sum", "value_percentile"):
        correlation["condition"]["field"] = field or correlation["condition"].get("field", "Amount")
    if kind == "value_percentile":
        correlation["condition"].setdefault("percentile", 50)
    return {"title": name, "name": name, "correlation": correlation}


def fixture_schema(tables=("Events", "NetworkEvents"), **overrides):
    """Partial declarations shared by custom scalar acceptance pipelines."""
    types = {
        field: "string"
        for field in (
            "Host",
            "Region",
            "User",
            "Server",
            "Case",
            "Marker",
            "Actor",
            "Account",
            "Computer",
            "Device",
            "File",
            "Target",
            "Destination",
            "ProcessUser",
            "ProcessHost",
            "ProcessTarget",
            "NetworkUser",
            "NetworkHost",
            "NetworkTarget",
            "Machine",
            "SourceUser",
            "ResolvedUser",
            "CommandLine",
            "Image",
            "Process Host",
            "Process Actor",
            "Network Host",
            "Network Actor",
            "Machine Name",
            "Actor Name",
            "Process Actor",
            "Network Actor",
            "Group",
            "Value",
            "Other",
            "Tenant",
            "Scope",
            "NetworkScope",
            "ProcessCase",
        )
    }
    types.update(
        {
            field: "datetime"
            for field in (
                "WindowEnd",
                "Timestamp",
                "EventTime",
                "ProcessTime",
                "NetworkTime",
                "Event Time",
                "Process Time",
                "Network Time",
            )
        }
    )
    types.update({field: "long" for field in ("Amount", "Size", "Length", "Low", "High")})
    types.update(overrides)
    return BaseSchema(
        tables={
            table: TableSchema({name: FieldInfo(kind, "Scalar fixture column") for name, kind in types.items()})
            for table in tables
        }
    )


def convert_documents(documents, *, mappings=None, tables=("Events", "NetworkEvents"), schema=None):
    pipeline = ProcessingPipeline.from_dict(
        {
            "transformations": [
                {"type": "set_state", "key": "query_table", "val": tables[0]},
                {
                    "type": "set_state",
                    "key": "query_table",
                    "val": tables[1],
                    "rule_conditions": [{"type": "logsource", "product": "network"}],
                },
            ]
        }
    )
    pipeline.vars["kusto_schema"] = schema or fixture_schema(tables)
    if mappings is not None:
        pipeline.vars["kusto_field_mappings"] = mappings
    return KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    )


def numeric_documents(kind="value_sum", groups=(), field="Amount"):
    source = source_document()
    source["fields"] = ["User", "Amount"]
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    parent = correlation_document("parent", kind, ["child"], groups=groups, condition={"field": field, "gte": 0})
    return [source, child, parent]
