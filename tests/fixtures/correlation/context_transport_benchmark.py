"""Freeze #53 scale workloads through public collection conversion.

The oracle models correlation summaries and complete retained relationships
without importing backend internals.  Every frozen query ends in an empty
multiset diagnostic, so the established benchmark runner can measure it while
still rejecting missing, extra, or duplicated results.
"""

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.processing.pipeline import ProcessingPipeline
from tests.fixtures.correlation import benchmark

DIMENSIONS = {
    "density",
    "groups",
    "repetition",
    "selectivity",
    "context-fanout",
    "child-reuse",
    "chain-depth",
    "numeric-pairing",
}


def convert(documents):
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            "Events": TableSchema(
                fields={
                    name: FieldInfo(kind, "ticket 53 scale scalar")
                    for name, kind in {
                        "Timestamp": "datetime",
                        "Host": "string",
                        "Marker": "string",
                        "Amount": "long",
                        "User": "string",
                        "Server": "string",
                    }.items()
                }
            )
        }
    )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(documents))
    )
    return query


def input_query(events):
    records = [
        f'datetime({benchmark.timestamp(row["second"])}), {json.dumps(row["Host"])}, '
        f'{json.dumps(row["Marker"])}, {row["Amount"]}, {json.dumps(row["User"])}, '
        f'{json.dumps(row["Server"])}'
        for row in events
    ]
    return (
        "let Events = datatable(Timestamp:datetime, Host:string, Marker:string, Amount:long, User:string, "
        "Server:string) [\n" + ",\n".join(records) + "\n];\n"
    )


def rows(size, groups=1, step=1, repeated=True):
    return [
        {
            "second": index * step,
            "Host": f"h{index % groups}",
            "Marker": "match",
            "User": f"u{index % 4}" if repeated else f"u{index}",
            "Server": f"s{(index // 2) % 7}" if repeated else f"s{index}",
            "Amount": 1 + index % 11,
        }
        for index in range(size)
    ]


def source_document(fields):
    return {
        "title": "ScaleSource",
        "name": "ScaleSource",
        "logsource": {"product": "benchmark"},
        "detection": {"selection": {"Marker": "match"}, "condition": "selection"},
        "fields": list(fields),
    }


def correlation_document(name, sources, groups, fields, kind="event_count", threshold=1):
    condition = {"gte": threshold}
    if kind == "value_sum":
        condition["field"] = "Amount"
    return {
        "title": name,
        "name": name,
        "correlation": {
            "type": kind,
            "rules": list(sources),
            "group-by": list(groups),
            "timespan": "5m",
            "condition": condition,
            "generate": False,
        },
        "fields": list(fields),
    }


def child_matches(events, threshold, context_fields):
    output = []
    for host in sorted({row["Host"] for row in events}):
        grouped = [row for row in events if row["Host"] == host]
        for end in sorted({row["second"] for row in grouped}):
            window = [row for row in grouped if end - 300 <= row["second"] <= end]
            if len(window) < threshold:
                continue
            evidence = sorted({tuple(row[field] for field in context_fields) for row in window}, key=repr)
            output.append(
                {
                    "id": f"child:{host}:{end}",
                    "second": end,
                    "groups": {"Host": host},
                    "metric": len(window),
                    "evidence": evidence,
                    "fields": context_fields,
                }
            )
    return output


def _expanded(match, group_fields):
    if not group_fields:
        return [((), match["evidence"])]
    indexes = [match["fields"].index(field) for field in group_fields]
    grouped = {}
    for relationship in match["evidence"]:
        key = tuple(relationship[index] for index in indexes)
        grouped.setdefault(key, []).append(relationship)
    return sorted(grouped.items(), key=repr)


def event_count_parent(matches, name, group_fields, context_fields):
    contributions = []
    for match in matches:
        for group, evidence in _expanded(match, group_fields):
            contributions.append(
                {
                    "id": f'{match["id"]}:{name}:{group!r}',
                    "second": match["second"],
                    "group": group,
                    "evidence": evidence,
                    "fields": match["fields"],
                }
            )
    output = []
    for group in sorted({row["group"] for row in contributions}, key=repr):
        grouped = [row for row in contributions if row["group"] == group]
        for end in sorted({row["second"] for row in grouped}):
            window = [row for row in grouped if end - 300 <= row["second"] <= end]
            evidence = sorted({item for row in window for item in row["evidence"]}, key=repr)
            output.append(
                {
                    "id": f"{name}:{group!r}:{end}",
                    "second": end,
                    "groups": dict(zip(group_fields, group)),
                    "metric": len(window),
                    "evidence": evidence,
                    "fields": context_fields,
                }
            )
    return output


def value_sum_parent(matches, name, group_fields, context_fields):
    observations = []
    amount_index = context_fields.index("Amount")
    for match in matches:
        for group, evidence in _expanded(match, group_fields):
            amounts = {relationship[amount_index] for relationship in evidence}
            observations.append(
                {
                    "id": f'{match["id"]}:{name}:{group!r}',
                    "second": match["second"],
                    "group": group,
                    "amounts": amounts,
                    "evidence": evidence,
                }
            )
    output = []
    for group in sorted({row["group"] for row in observations}, key=repr):
        grouped = [row for row in observations if row["group"] == group]
        for end in sorted({row["second"] for row in grouped}):
            window = [row for row in grouped if end - 300 <= row["second"] <= end]
            evidence = sorted({item for row in window for item in row["evidence"]}, key=repr)
            output.append(
                {
                    "id": f"{name}:{group!r}:{end}",
                    "second": end,
                    "groups": dict(zip(group_fields, group)),
                    "metric": sum(sum(row["amounts"]) for row in window),
                    "evidence": evidence,
                    "fields": context_fields,
                }
            )
    return output


def combine_ungrouped(matches, name="SharedRoot"):
    contributions = [
        {"id": match["id"], "second": match["second"], "group": (), "evidence": match["evidence"]} for match in matches
    ]
    output = []
    for end in sorted({row["second"] for row in contributions}):
        window = [row for row in contributions if end - 300 <= row["second"] <= end]
        output.append(
            {
                "id": f"{name}:{end}",
                "second": end,
                "groups": {},
                "metric": len(window),
                "evidence": sorted({item for row in window for item in row["evidence"]}, key=repr),
                "fields": matches[0]["fields"] if matches else (),
            }
        )
    return output


def _literal(value):
    return json.dumps(value) if isinstance(value, str) else str(value)


def diagnostic(query, expected, group_fields, metric):
    columns = ["WindowEnd:datetime", *(f"{field}:string" for field in group_fields), "Metric:long"]
    records = []
    for row in expected:
        values = [f"datetime({benchmark.timestamp(row['second'])})"]
        values.extend(_literal(row["groups"][field]) for field in group_fields)
        values.append(str(int(row["metric"])))
        records.append(", ".join(values))
    expected_relation = "datatable(" + ", ".join(columns) + ") [\n" + ",\n".join(records) + "\n]"
    actual_fields = ", ".join(["WindowEnd", *group_fields, f"Metric=tolong({metric})"])
    comparison_fields = ", ".join(["WindowEnd", *group_fields, "Metric"])
    return (
        f"let Actual = () {{\n{query}\n}};\n"
        f"let Observed = Actual() | project {actual_fields};\n"
        f"let Expected = {expected_relation};\n"
        "union (Observed | extend Delta=long(1)), (Expected | extend Delta=long(-1))\n"
        f"| summarize Delta=sum(Delta) by {comparison_fields}\n| where Delta != 0\n"
    )


def transport_model(matches):
    summary_rows = len(matches)
    evidence_rows = sum(len(match["evidence"]) for match in matches)
    summary_bytes = sum(
        len(json.dumps([match["second"], match["groups"], match["metric"]], sort_keys=True)) for match in matches
    )
    union_evidence_bytes = sum(
        len(json.dumps([match["second"], match["groups"], match["metric"], relationship], sort_keys=True))
        for match in matches
        for relationship in match["evidence"]
    )
    separate_evidence_bytes = sum(
        len(json.dumps([match["id"], relationship], sort_keys=True))
        for match in matches
        for relationship in match["evidence"]
    )
    return {
        "summary_rows": summary_rows,
        "evidence_rows": evidence_rows,
        "union_rows": summary_rows + evidence_rows,
        "separate_rows_when_both_consumed": summary_rows + evidence_rows,
        "union_estimated_bytes": summary_bytes + union_evidence_bytes,
        "separate_estimated_bytes_when_both_consumed": summary_bytes + separate_evidence_bytes,
        "operator_cardinality": "not exposed by the local Kusto response protocol",
    }


def case(name, events, threshold, context_fields, dimensions, mode="event", depth=1):
    child = child_matches(events, threshold, context_fields)
    documents = [source_document(context_fields)]
    documents.append(
        correlation_document("ScaleChild", ["ScaleSource"], ("Host",), context_fields, threshold=threshold)
    )
    current = child
    group_fields = ("User",)
    metric = "EventCount"
    if mode == "numeric":
        documents.append(
            correlation_document("NumericParent", ["ScaleChild"], group_fields, context_fields, kind="value_sum")
        )
        current = value_sum_parent(child, "NumericParent", group_fields, context_fields)
        metric = "ValueSum"
    elif mode == "shared":
        documents.extend(
            [
                correlation_document("ParentByUser", ["ScaleChild"], ("User",), context_fields),
                correlation_document("ParentByServer", ["ScaleChild"], ("Server",), context_fields),
                correlation_document("SharedRoot", ["ParentByUser", "ParentByServer"], (), context_fields),
            ]
        )
        by_user = event_count_parent(child, "ParentByUser", ("User",), context_fields)
        by_server = event_count_parent(child, "ParentByServer", ("Server",), context_fields)
        current = combine_ungrouped([*by_user, *by_server])
        group_fields = ()
    else:
        source_name = "ScaleChild"
        for level in range(depth):
            parent_name = f"ContextParent{level + 1}"
            documents.append(correlation_document(parent_name, [source_name], group_fields, context_fields))
            current = event_count_parent(current, parent_name, group_fields, context_fields)
            source_name = parent_name
    return {
        "name": name,
        "rows": events,
        "documents": documents,
        "expected": current,
        "group_fields": group_fields,
        "metric": metric,
        "dimensions": sorted(dimensions),
        "transport_model": transport_model(child),
    }


def cases():
    common = ("User", "Server", "Amount")
    return [
        case("sparse-64", rows(64, step=120), 2, common, {"density"}),
        case("dense-64", rows(64), 2, common, {"density"}),
        case("dense-256", rows(256), 2, common, {"density"}),
        case("many-groups-256", rows(256, groups=32), 2, common, {"groups"}),
        case("hot-repeated-256", rows(256, repeated=True), 2, common, {"repetition"}),
        case("hot-unique-256", rows(256, repeated=False), 2, common, {"repetition"}),
        case("low-selectivity-256", rows(256), 200, common, {"selectivity"}),
        case("high-selectivity-256", rows(256), 2, common, {"selectivity"}),
        case("one-context-field", rows(128), 2, ("User",), {"context-fanout"}),
        case("three-context-fields", rows(128), 2, common, {"context-fanout"}),
        case("shared-child-consumers", rows(128), 2, common, {"child-reuse"}, mode="shared"),
        case("chain-depth-one", rows(128), 2, common, {"chain-depth"}, depth=1),
        case("chain-depth-three", rows(128), 2, common, {"chain-depth"}, depth=3),
        case("numeric-pairing-repeated", rows(128, repeated=True), 2, common, {"numeric-pairing"}, mode="numeric"),
        case("numeric-pairing-unique", rows(128, repeated=False), 2, common, {"numeric-pairing"}, mode="numeric"),
    ]


def freeze(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise FileExistsError("frozen manifest already exists; choose a new output directory")
    record = {
        "format": 1,
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "oracle": "independent inclusive-window summary and complete-relationship model",
        "dimensions": sorted(DIMENSIONS),
        "workloads": [],
    }
    for item in cases():
        public_query = convert(item["documents"])
        query = input_query(item["rows"]) + diagnostic(
            public_query, item["expected"], item["group_fields"], item["metric"]
        )
        fixture = {
            "name": item["name"],
            "schema": {},
            "expected": [],
            "dimensions": item["dimensions"],
            "input_rows": len(item["rows"]),
            "expected_rows": len(item["expected"]),
            "transport_model": item["transport_model"],
        }
        fixture_path = destination / f'{item["name"]}.json'
        query_path = destination / f'{item["name"]}.kql'
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
        query_path.write_text(query)
        operators = Counter(re.findall(r"\|\s*([a-z][\w-]*)", public_query))
        record["workloads"].append(
            {
                "name": item["name"],
                "fixture": fixture_path.name,
                "fixture_sha256": benchmark.sha(fixture_path.read_bytes()),
                "workload_sha256": benchmark.sha(fixture_path.read_bytes()),
                "query": query_path.name,
                "query_sha256": benchmark.sha(query_path.read_bytes()),
                "query_bytes": len(query.encode()),
                "query_lines": len(query.splitlines()),
                "operators": dict(sorted(operators.items())),
                "input_rows": len(item["rows"]),
                "expected_rows": len(item["expected"]),
                "dimensions": item["dimensions"],
                "transport_model": item["transport_model"],
            }
        )
    (destination / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    freeze(parser.parse_args().output)
