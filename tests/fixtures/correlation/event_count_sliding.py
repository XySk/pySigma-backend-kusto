"""Exact weighted-count acceptance with literal, independently worked rows."""

import json
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.fixtures.correlation.generate import HEADER, check


def fractional_fixture():
    data = """// Host a anchors/counts: 00:00=2, 00:05=4, 00:05+tick=3, 00:20=1.
// Host b has independent single-match windows. No expiry is an evaluation anchor.
let Events = datatable(Timestamp:datetime, Host:string, Marker:string) [
 datetime(2026-01-01T00:00:00), "a", "match",
 datetime(2026-01-01T00:00:00), "a", "match",
 datetime(2026-01-01T00:05:00), "a", "match",
 datetime(2026-01-01T00:05:00), "a", "match",
 datetime(2026-01-01T00:05:00.0000001), "a", "match",
 datetime(2026-01-01T00:20:00), "a", "match",
 datetime(2026-01-01T00:00:00), "b", "match",
 datetime(2026-01-01T00:05:00.0000001), "b", "match"
];
"""
    operators = ["gt", "gte", "lt", "lte", "eq", "neq"]
    functions = []
    for operator in operators:
        (query,) = convert_documents(
            [
                source_document(),
                correlation_document("count", "event_count", ["source-rule"], condition={operator: 2.5}),
            ]
        )
        functions.append(f"let {operator}_fraction = () {{\n{query}\n}};\n")
    actual = (
        "let Actual = union "
        + ", ".join(f'({operator}_fraction() | extend Scenario = "{operator}")' for operator in operators)
        + ";\n"
    )
    # Each entry explicitly names the qualifying anchor and actual integer count.
    expected_rows = {
        "gt": [("a", "00:05:00", 4), ("a", "00:05:00.0000001", 3)],
        "gte": [("a", "00:05:00", 4), ("a", "00:05:00.0000001", 3)],
        "lt": [("a", "00:00:00", 2), ("a", "00:20:00", 1), ("b", "00:00:00", 1), ("b", "00:05:00.0000001", 1)],
        "lte": [("a", "00:00:00", 2), ("a", "00:20:00", 1), ("b", "00:00:00", 1), ("b", "00:05:00.0000001", 1)],
        "eq": [],
        "neq": [
            ("a", "00:00:00", 2),
            ("a", "00:05:00", 4),
            ("a", "00:05:00.0000001", 3),
            ("a", "00:20:00", 1),
            ("b", "00:00:00", 1),
            ("b", "00:05:00.0000001", 1),
        ],
    }
    rows = [
        f'"{op}", datetime(2026-01-01T{end}), "{host}", {count}'
        for op, matches in expected_rows.items()
        for host, end, count in matches
    ]
    expected = (
        "let Expected = datatable(Scenario:string, WindowEnd:datetime, Host:string, EventCount:long) [\n"
        + ",\n".join(rows)
        + "\n]\n| extend WindowStart = WindowEnd - 5m;\n"
    )
    return (
        HEADER
        + data
        + "".join(functions)
        + actual
        + expected
        + check("Actual", "Expected", ["Scenario", "WindowStart", "WindowEnd", "Host", "EventCount"])
    )


def empty_fixture():
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("count", "event_count", ["source-rule"], groups=(), condition={"eq": 0}),
        ]
    )
    return (
        HEADER
        + "let Events = datatable(Timestamp:datetime, Marker:string) [];\nlet Actual = () {\n"
        + query
        + "\n};\nActual()\n"
    )


def fixtures():
    return {
        "event_count_fractional.kql": fractional_fixture(),
        "event_count_empty.kql": empty_fixture(),
        "event_count_real_groups.kql": real_groups_fixture(),
    }


def real_groups_fixture():
    from tests.correlation_helpers import fixture_schema

    (query,) = convert_documents(
        [
            source_document(),
            correlation_document("count", "event_count", ["source-rule"], groups=("Group",), condition={"gte": 1}),
        ],
        schema=fixture_schema(Group="real"),
    )
    data = """// Native real groups include NaNs, infinities and signed zero.
let Events=datatable(Timestamp:datetime,Group:real,Marker:string) [
 datetime(2026-01-01T00:00:00),real(nan),"match",
 datetime(2026-01-01T00:01:00),real(nan),"match",
 datetime(2026-01-01T00:00:00),real(+inf),"match",
 datetime(2026-01-01T00:01:00),real(+inf),"match",
 datetime(2026-01-01T00:00:00),real(-inf),"match",
 datetime(2026-01-01T00:01:00),real(-inf),"match",
 datetime(2026-01-01T00:00:00),real(-0.0),"match",
 datetime(2026-01-01T00:01:00),real(+0.0),"match"
];
"""
    expected = """let Expected=datatable(WindowEnd:datetime,EventCount:long) [
 datetime(2026-01-01T00:00:00),1,datetime(2026-01-01T00:01:00),2
]
| extend Keys=dynamic(["NaN","inf","-inf","0.0"])
| mv-expand Group=Keys to typeof(string)
| project WindowEnd,EventCount,Group;
let Observed=Actual() | project WindowEnd,EventCount,Group=tostring(Group);
union (Expected | extend Delta=-1),(Observed | extend Delta=1)
| summarize Delta=sum(Delta) by WindowEnd,EventCount,Group
| where Delta!=0
"""
    return HEADER + data + "let Actual=(){\n" + query + "\n};\n" + expected


if __name__ == "__main__":
    from tests.fixtures.correlation.runtime import manifest

    destination = Path(__file__).parent
    for name, query in fixtures().items():
        (destination / name).write_text(query)
    prepared = manifest(destination)
    prepared["fixtures"] = [item for item in prepared["fixtures"] if item["name"] in fixtures()]
    (destination / "event-count-manifest.json").write_text(json.dumps(prepared, indent=2) + "\n")
