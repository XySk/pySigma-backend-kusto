"""Temporal presence examples with literal expected source-type counts."""

import hashlib
import json
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document


def checked(query, rows, expected, *, group_type="string"):
    return (
        "// Public Sigma conversion; expected rows are worked inclusive-window examples.\n"
        f"let Events=datatable(Timestamp:datetime,Host:{group_type},Marker:string) [\n{rows}\n];\n"
        f"let Actual=(){{\n{query}\n}};\n"
        f"let Expected=datatable(WindowEnd:datetime,Host:{group_type},RuleCount:long) [\n{expected}\n];\n"
        "union (Actual() | project WindowEnd,Host,RuleCount | extend Delta=1), (Expected | extend Delta=-1)\n"
        "| summarize Delta=sum(Delta) by WindowEnd,Host,RuleCount\n| where Delta!=0\n"
    )


def documents(condition=None, *, groups=("Host",)):
    return [
        *(source_document(name, name) for name in ("A", "B", "C")),
        correlation_document("presence", "temporal", ["A", "B", "C"], groups=groups, condition=condition),
    ]


def fixtures():
    rows = '''datetime(2026-01-01T00:00:00Z),"boundary","A",
        datetime(2026-01-01T00:00:00Z),"boundary","A",
        datetime(2026-01-01T00:05:00Z),"boundary","B",
        datetime(2026-01-01T00:05:00.0000001Z),"boundary","B",
        datetime(2026-01-01T00:11:00Z),"boundary","C",
        datetime(2026-01-01T00:00:00Z),"ties","A",
        datetime(2026-01-01T00:00:00Z),"ties","B",
        datetime(2026-01-01T00:00:00Z),"ties","C",
        datetime(2026-01-01T00:01:00Z),"ties","A",
        datetime(null),"ties","C",
        datetime(2026-01-01T00:00:00Z),"","A"'''
    one = [
        'datetime(2026-01-01T00:00:00Z),"boundary",1',
        'datetime(2026-01-01T00:05:00.0000001Z),"boundary",1',
        'datetime(2026-01-01T00:11:00Z),"boundary",1',
    ]
    two = ['datetime(2026-01-01T00:05:00Z),"boundary",2']
    three = ['datetime(2026-01-01T00:00:00Z),"ties",3', 'datetime(2026-01-01T00:01:00Z),"ties",3']
    cases = {
        "omitted": (None, three),
        "gt": ({"gt": 2}, three),
        "gte": ({"gte": 2}, two + three),
        "lt": ({"lt": 2}, one),
        "lte": ({"lte": 2}, one + two),
        "eq": ({"eq": 2}, two),
        "neq": ({"neq": 2}, one + three),
        "fractional_eq": ({"eq": 1.5}, []),
        "fractional_neq": ({"neq": 1.5}, one + two + three),
    }
    result = {}
    for name, (condition, expected) in cases.items():
        (query,) = convert_documents(documents(condition))
        result[f"presence_{name}.kql"] = checked(query, rows, ",\n".join(expected))
    (query,) = convert_documents(documents({"gte": 2}), schema=fixture_schema(Host="real"))
    result["presence_real_groups.kql"] = checked(
        query,
        '''datetime(2026-01-01T00:00:00Z),real(nan),"A",
        datetime(2026-01-01T00:01:00Z),real(nan),"B",
        datetime(2026-01-01T00:00:00Z),real(+inf),"B",
        datetime(2026-01-01T00:00:00Z),real(-inf),"A",
        datetime(2026-01-01T00:00:00Z),0.0,"A",
        datetime(2026-01-01T00:01:00Z),-0.0,"B"''',
        """datetime(2026-01-01T00:01:00Z),real(nan),2,
        datetime(2026-01-01T00:01:00Z),0.0,2""",
        group_type="real",
    )
    (query,) = convert_documents(documents({"gte": 2}, groups=()))
    result["presence_ungrouped.kql"] = checked(
        query + '\n| extend Host="all"',
        '''datetime(2026-01-01T00:00:00Z),"a","A",
        datetime(2026-01-01T00:05:00Z),"b","B",
        datetime(2026-01-01T00:05:00.0000001Z),"c","C"''',
        """datetime(2026-01-01T00:05:00Z),"all",2,
        datetime(2026-01-01T00:05:00.0000001Z),"all",2""",
    )
    mixed = [
        source_document("raw", "A"), source_document("B", "B"),
        correlation_document("child", "event_count", ["raw"], condition={"gte": 2}),
        correlation_document("parent", "temporal", ["child", "B"], groups=("Actor",),
                             aliases={"Actor": {"child": "Host", "B": "Device"}}),
    ]
    query, = convert_documents(mixed)
    result["presence_mixed_alias.kql"] = checked(
        query + "\n| project WindowEnd,Host=Actor,RuleCount",
        '''datetime(2026-01-01T00:00:00Z),"g","A",
        datetime(2026-01-01T00:01:00Z),"g","A",
        datetime(2026-01-01T00:06:00Z),"g","B",
        datetime(2026-01-01T00:06:00.0000001Z),"g","B"''',
        'datetime(2026-01-01T00:06:00Z),"g",2',
    )
    result["presence_mixed_alias.kql"] = result["presence_mixed_alias.kql"].replace(
        "];\nlet Actual", "] | extend Device=Host;\nlet Actual")
    return result


if __name__ == "__main__":
    directory = Path(__file__).parent
    files = fixtures()
    for name, query in files.items():
        (directory / name).write_text(query)
    manifest = {
        "fixtures": [
            {"name": name, "sha256": hashlib.sha256(query.encode()).hexdigest()} for name, query in files.items()
        ]
    }
    (directory / "presence-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
