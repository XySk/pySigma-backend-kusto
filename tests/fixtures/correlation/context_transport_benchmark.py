"""Freeze #53 scale workloads through public collection conversion.

The oracle models correlation summaries and complete retained relationships
without importing backend internals.  Every frozen query ends in an empty
multiset diagnostic, so the established benchmark runner can measure it while
still rejecting missing, extra, or duplicated results.
"""

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.processing.pipeline import ProcessingPipeline
from tests.fixtures.correlation import benchmark
from tests.fixtures.correlation.focused_benchmark_manifest import FocusedBenchmarkManifest

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

Scalar: TypeAlias = str | int
Relationship: TypeAlias = tuple[Scalar, ...]
Group: TypeAlias = tuple[Scalar, ...]


@dataclass(frozen=True)
class Match:
    """One modeled correlation match with complete retained relationships."""

    id: str
    second: int
    groups: dict[str, Scalar]
    metric: int
    evidence: tuple[Relationship, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Contribution:
    """One child contribution after expansion into a resulting parent group."""

    second: int
    group: Group
    evidence: tuple[Relationship, ...]
    amounts: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Transport:
    """One scoped context relation and its references from one consuming parent."""

    name: str
    matches: tuple[Match, ...]
    consumer: str
    summary_references: int
    evidence_references: int


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
    fields = tuple(context_fields)
    for host in sorted({row["Host"] for row in events}):
        grouped = [row for row in events if row["Host"] == host]
        for end in sorted({row["second"] for row in grouped}):
            window = [row for row in grouped if end - 300 <= row["second"] <= end]
            if len(window) < threshold:
                continue
            evidence = tuple(sorted({tuple(row[field] for field in fields) for row in window}, key=repr))
            output.append(Match(f"child:{host}:{end}", end, {"Host": host}, len(window), evidence, fields))
    return tuple(output)


def _expanded(match, group_fields):
    if not group_fields:
        return [((), match.evidence)]
    indexes = [match.fields.index(field) for field in group_fields]
    grouped = {}
    for relationship in match.evidence:
        key = tuple(relationship[index] for index in indexes)
        grouped.setdefault(key, []).append(relationship)
    return [(group, tuple(evidence)) for group, evidence in sorted(grouped.items(), key=repr)]


def event_count_parent(matches, name, group_fields, context_fields):
    contributions = []
    for match in matches:
        for group, evidence in _expanded(match, group_fields):
            contributions.append(Contribution(match.second, group, evidence))
    output = []
    for group in sorted({row.group for row in contributions}, key=repr):
        grouped = [row for row in contributions if row.group == group]
        for end in sorted({row.second for row in grouped}):
            window = [row for row in grouped if end - 300 <= row.second <= end]
            evidence = tuple(sorted({item for row in window for item in row.evidence}, key=repr))
            output.append(
                Match(
                    f"{name}:{group!r}:{end}",
                    end,
                    dict(zip(group_fields, group)),
                    len(window),
                    evidence,
                    tuple(context_fields),
                )
            )
    return tuple(output)


def value_sum_parent(matches, name, group_fields, context_fields):
    observations = []
    amount_index = context_fields.index("Amount")
    for match in matches:
        for group, evidence in _expanded(match, group_fields):
            amounts = {relationship[amount_index] for relationship in evidence}
            observations.append(
                Contribution(
                    match.second,
                    group,
                    evidence,
                    frozenset(amounts),
                )
            )
    output = []
    for group in sorted({row.group for row in observations}, key=repr):
        grouped = [row for row in observations if row.group == group]
        for end in sorted({row.second for row in grouped}):
            window = [row for row in grouped if end - 300 <= row.second <= end]
            evidence = tuple(sorted({item for row in window for item in row.evidence}, key=repr))
            output.append(
                Match(
                    f"{name}:{group!r}:{end}",
                    end,
                    dict(zip(group_fields, group)),
                    sum(sum(row.amounts) for row in window),
                    evidence,
                    tuple(context_fields),
                )
            )
    return tuple(output)


def combine_ungrouped(matches, name="SharedRoot"):
    contributions = [Contribution(match.second, (), match.evidence) for match in matches]
    output = []
    for end in sorted({row.second for row in contributions}):
        window = [row for row in contributions if end - 300 <= row.second <= end]
        output.append(
            Match(
                f"{name}:{end}",
                end,
                {},
                len(window),
                tuple(sorted({item for row in window for item in row.evidence}, key=repr)),
                matches[0].fields if matches else (),
            )
        )
    return tuple(output)


def _literal(value):
    return json.dumps(value) if isinstance(value, str) else str(value)


def diagnostic(query, expected, group_fields, metric):
    columns = ["WindowEnd:datetime", *(f"{field}:string" for field in group_fields), "Metric:long"]
    records = []
    for row in expected:
        values = [f"datetime({benchmark.timestamp(row.second)})"]
        values.extend(_literal(row.groups[field]) for field in group_fields)
        values.append(str(int(row.metric)))
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


def _relation_transport(transport):
    matches = transport.matches
    summary_rows = len(matches)
    evidence_rows = sum(len(match.evidence) for match in matches)
    summary_bytes = sum(
        len(json.dumps([match.id, match.second, match.groups, match.metric], sort_keys=True)) for match in matches
    )
    union_evidence_bytes = sum(
        len(json.dumps([match.id, match.second, match.groups, match.metric, relationship], sort_keys=True))
        for match in matches
        for relationship in match.evidence
    )
    separate_evidence_bytes = sum(
        len(json.dumps([match.id, relationship], sort_keys=True))
        for match in matches
        for relationship in match.evidence
    )
    references = transport.summary_references + transport.evidence_references
    current_rows = (summary_rows + evidence_rows) * references
    candidate_rows = summary_rows * transport.summary_references + evidence_rows * transport.evidence_references
    current_bytes = (summary_bytes + union_evidence_bytes) * references
    candidate_bytes = (
        summary_bytes * transport.summary_references + separate_evidence_bytes * transport.evidence_references
    )
    return {
        "name": transport.name,
        "consumer": transport.consumer,
        "summary_references": transport.summary_references,
        "evidence_references": transport.evidence_references,
        "summary_rows": summary_rows,
        "evidence_rows": evidence_rows,
        "summary_estimated_bytes": summary_bytes,
        "union_evidence_estimated_bytes": union_evidence_bytes,
        "separate_evidence_estimated_bytes": separate_evidence_bytes,
        "current_rows_across_uses": current_rows,
        "candidate_rows_across_uses": candidate_rows,
        "current_estimated_bytes_across_uses": current_bytes,
        "candidate_estimated_bytes_across_uses": candidate_bytes,
    }


def transport_model(transports):
    relations = [_relation_transport(transport) for transport in transports]
    first = relations[0]
    return {
        # Preserve the original child-relation fields for existing consumers.
        "summary_rows": first["summary_rows"],
        "evidence_rows": first["evidence_rows"],
        "union_rows": first["summary_rows"] + first["evidence_rows"],
        "separate_rows_when_both_consumed": first["summary_rows"] + first["evidence_rows"],
        "union_estimated_bytes": first["summary_estimated_bytes"] + first["union_evidence_estimated_bytes"],
        "separate_estimated_bytes_when_both_consumed": first["summary_estimated_bytes"]
        + first["separate_evidence_estimated_bytes"],
        "relations": relations,
        "current": {
            "rows_across_uses": sum(relation["current_rows_across_uses"] for relation in relations),
            "estimated_bytes_across_uses": sum(
                relation["current_estimated_bytes_across_uses"] for relation in relations
            ),
        },
        "candidate": {
            "rows_across_uses": sum(relation["candidate_rows_across_uses"] for relation in relations),
            "estimated_bytes_across_uses": sum(
                relation["candidate_estimated_bytes_across_uses"] for relation in relations
            ),
        },
        "operator_cardinality": "not exposed by the local Kusto response protocol",
    }


def _reference_counts(context_fields, group_fields, matching_role):
    reconstructs_context = int(any(field not in group_fields for field in context_fields))
    return (
        reconstructs_context + int(matching_role == "summary"),
        reconstructs_context + int(matching_role == "evidence"),
    )


def _validate_transport_references(query, transports):
    expected = {}
    for transport in transports:
        references = expected.setdefault(transport.name, [0, 0])
        references[0] += transport.summary_references
        references[1] += transport.evidence_references
    for name, (summary_references, evidence_references) in expected.items():
        prefix = re.escape(f"{name}Results") + r'(?:\n| )\| where \["ContextRowKind(?:_\d+)?"\] == '
        assert len(re.findall(prefix + r'"Summary"', query)) == summary_references
        assert len(re.findall(prefix + r'"ContextEvidence"', query)) == evidence_references


def case(name, events, threshold, context_fields, dimensions, mode="event", depth=1):
    child = child_matches(events, threshold, context_fields)
    transports = []
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
        transports.append(
            Transport(
                "ScaleChild",
                child,
                "NumericParent",
                *_reference_counts(context_fields, group_fields, "evidence"),
            )
        )
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
        transports.extend(
            (
                Transport(
                    "ScaleChild",
                    child,
                    "ParentByUser",
                    *_reference_counts(context_fields, ("User",), "evidence"),
                ),
                Transport(
                    "ScaleChild",
                    child,
                    "ParentByServer",
                    *_reference_counts(context_fields, ("Server",), "evidence"),
                ),
                Transport(
                    "ParentByUser",
                    by_user,
                    "SharedRoot",
                    *_reference_counts(context_fields, (), "summary"),
                ),
                Transport(
                    "ParentByServer",
                    by_server,
                    "SharedRoot",
                    *_reference_counts(context_fields, (), "summary"),
                ),
            )
        )
        group_fields = ()
    else:
        source_name = "ScaleChild"
        for level in range(depth):
            parent_name = f"ContextParent{level + 1}"
            documents.append(correlation_document(parent_name, [source_name], group_fields, context_fields))
            transports.append(
                Transport(
                    source_name,
                    current,
                    parent_name,
                    *_reference_counts(
                        context_fields,
                        group_fields,
                        "evidence" if level == 0 else "summary",
                    ),
                )
            )
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
        "transports": transports,
        "transport_model": transport_model(transports),
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
    manifest = FocusedBenchmarkManifest.begin(
        destination,
        oracle="independent inclusive-window summary and complete-relationship model",
        dimensions=sorted(DIMENSIONS),
    )
    for item in cases():
        public_query = convert(item["documents"])
        _validate_transport_references(public_query, item["transports"])
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
        manifest.add(
            item["name"],
            fixture,
            query,
            public_query,
            input_rows=len(item["rows"]),
            expected_rows=len(item["expected"]),
            dimensions=item["dimensions"],
            transport_model=item["transport_model"],
        )
    manifest.write()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    freeze(parser.parse_args().output)
