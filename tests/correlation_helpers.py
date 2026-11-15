"""Shared Sigma inputs and public conversion for tests and prepared fixtures."""

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
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


def convert_documents(documents, *, mappings=None, tables=("Events", "NetworkEvents")):
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
    if mappings is not None:
        pipeline.vars["kusto_field_mappings"] = mappings
    return KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    )
