"""Standard mapping fixtures with independently worked summary expectations."""

from pathlib import Path

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.processing.pipeline import ProcessingPipeline
from tests.correlation_helpers import correlation_document, source_document


def fixture(parent=False):
    first = source_document(product="process")
    first["fields"] = ["User"]
    first["detection"]["condition"] = ["selection", "selection"]
    second = source_document("network-rule", product="network")
    second["fields"] = ["SourceUser"]
    correlation = correlation_document(
        "mapped",
        "value_sum",
        ["source-rule", "network-rule"],
        groups=("Host",),
        field="Amount",
        condition={"gte": 0},
        aliases={"User": {"source-rule": "User", "network-rule": "SourceUser"}},
    )
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
                {
                    "type": "field_name_mapping",
                    "mapping": {
                        "Timestamp": "Process Time",
                        "Host": "Process Host",
                        "User": "Process Actor",
                        "Amount": "Process Amount",
                        "Marker": "Process Marker",
                    },
                    "rule_conditions": [{"type": "logsource", "product": "process"}],
                },
                {
                    "type": "field_name_mapping",
                    "mapping": {
                        "Timestamp": "Network Time",
                        "Host": "Network Host",
                        "SourceUser": "Network Actor",
                        "Amount": "Network Amount",
                        "Marker": "Network Marker",
                    },
                    "rule_conditions": [{"type": "logsource", "product": "network"}],
                },
            ]
        }
    )
    documents = [first, second, correlation]
    if parent:
        documents.append(
            correlation_document("consumer", "event_count", ["mapped"], groups=("Host", "User"), condition={"gte": 1})
        )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    )
    result = (
        """// Generated public conversion; two process searches contribute separately.
let Events=datatable(['Process Time']:datetime, ['Process Host']:string, ['Process Actor']:string,
 ['Process Amount']:long, ['Process Marker']:string) [
 datetime(2026-01-01T12:00:00Z), "h", "Alice", 10, "match",
 datetime(2026-01-01T12:00:00Z), "h", "ignored", 100, "other"
];
let NetworkEvents=datatable(['Network Time']:datetime, ['Network Host']:string, ['Network Actor']:string,
 ['Network Amount']:long, ['Network Marker']:string) [
 datetime(2026-01-01T12:00:00Z), "h", "Bob", 20, "match"
];
let Actual=() {
"""
        + query
        + """
};
let Observed=Actual() | project WindowStart, WindowEnd, Host, Metric=tostring(ValueSum),
 Users=array_length(User), Alice=set_has_element(User,"Alice"), Bob=set_has_element(User,"Bob");
let Expected=datatable(WindowStart:datetime, WindowEnd:datetime, Host:string, Metric:string,
 Users:long, Alice:bool, Bob:bool) [
 datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "h", "40", 2, true, true
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowStart, WindowEnd, Host, Metric, Users, Alice, Bob
| where Delta!=0
"""
    )

    if not parent:
        return result
    return (
        result[: result.index("let Observed=")]
        + """let Observed=Actual() | project WindowStart, WindowEnd, Host=tostring(Host), Actor=tostring(User), EventCount;
let Expected=datatable(WindowStart:datetime, WindowEnd:datetime, Host:string, Actor:string, EventCount:long) [
 datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "h", "Alice", 1,
 datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "h", "Bob", 1
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowStart, WindowEnd, Host, Actor, EventCount
| where Delta!=0
"""
    )


if __name__ == "__main__":
    Path(__file__).with_name("standard_mappings.kql").write_text(fixture())

    Path(__file__).with_name("standard_mappings_parent.kql").write_text(fixture(parent=True))
