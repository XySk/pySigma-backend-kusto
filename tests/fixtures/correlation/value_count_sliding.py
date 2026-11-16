"""Independent acceptance rows for scalar active-interval value counts."""

import json
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.generate import HEADER, check


def fractional_fixture():
    data = """let Events=datatable(Timestamp:datetime,User:string,Marker:string) [
 datetime(2026-01-01T00:00:00),"a","match",
 datetime(2026-01-01T00:00:00),"b","match",
 datetime(2026-01-01T00:05:00),"c","match",
 datetime(2026-01-01T00:05:00.0000001),"","match",
 datetime(2026-01-01T00:20:00),"","match"
];
"""
    functions, calls = [], []
    for operator in ("gt", "gte", "lt", "lte", "eq", "neq"):
        (query,) = convert_documents(
            [
                source_document(),
                correlation_document(
                    "count", "value_count", ["source-rule"], groups=(), field="User", condition={operator: 2.5}
                ),
            ]
        )
        functions.append(f"let {operator}Fraction=(){{\n{query}\n}};\n")
        calls.append(f'({operator}Fraction() | extend Scenario="{operator}")')
    expected = """let Expected=datatable(Scenario:string,WindowEnd:datetime,ValueCount:long) [
 "gt",datetime(2026-01-01T00:05:00),3,
 "gte",datetime(2026-01-01T00:05:00),3,
 "lt",datetime(2026-01-01T00:00:00),2,
 "lt",datetime(2026-01-01T00:05:00.0000001),1,
 "lt",datetime(2026-01-01T00:20:00),0,
 "lte",datetime(2026-01-01T00:00:00),2,
 "lte",datetime(2026-01-01T00:05:00.0000001),1,
 "lte",datetime(2026-01-01T00:20:00),0,
 "neq",datetime(2026-01-01T00:00:00),2,
 "neq",datetime(2026-01-01T00:05:00),3,
 "neq",datetime(2026-01-01T00:05:00.0000001),1,
 "neq",datetime(2026-01-01T00:20:00),0
] | extend WindowStart=WindowEnd-5m;
"""
    return (
        HEADER
        + data
        + "".join(functions)
        + "let Actual=union "
        + ",".join(calls)
        + ";\n"
        + expected
        + check("Actual", "Expected", ["Scenario", "WindowStart", "WindowEnd", "ValueCount"])
    )


def real_values_fixture():
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document(
                "count",
                "value_count",
                ["source-rule"],
                groups=("Group",),
                field=["Value", "Other"],
                condition={"gte": 0},
            ),
        ],
        schema=fixture_schema(Group="real", Value="real", Other="real"),
    )
    data = """// Both group equality and counted-value equality follow native summarize, including NaN.
let Population=datatable(Timestamp:datetime,Value:real,Other:real) [
 datetime(2026-01-01T00:00:00),real(nan),real(0),
 datetime(2026-01-01T00:01:00),real(nan),real(1),
 datetime(2026-01-01T00:02:00),real(+inf),real(1),
 datetime(2026-01-01T00:05:00),real(-inf),real(null),
 datetime(2026-01-01T00:06:00),real(null),real(null),
 datetime(2026-01-01T00:06:00.0000001),real(-0.0),real(null),
 datetime(2026-01-01T00:07:00),real(+0.0),real(null),
 datetime(2026-01-01T00:20:00),real(null),real(null)
];
let Events=union (Population | extend Group=real(nan)),(Population | extend Group=real(+inf)),
 (Population | extend Group=real(-inf)),(Population | extend Group=real(0))
 | extend Marker="match";
"""
    expected = """let Expected=datatable(WindowEnd:datetime,ValueCount_Value:long,ValueCount_Other:long) [
 datetime(2026-01-01T00:00:00),1,1,
 datetime(2026-01-01T00:01:00),1,2,
 datetime(2026-01-01T00:02:00),2,2,
 datetime(2026-01-01T00:05:00),3,2,
 datetime(2026-01-01T00:06:00),3,1,
 datetime(2026-01-01T00:06:00.0000001),3,1,
 datetime(2026-01-01T00:07:00),3,1,
 datetime(2026-01-01T00:20:00),0,0
] | extend Keys=dynamic(["NaN","inf","-inf","0.0"])
 | mv-expand Group=Keys to typeof(string) | project-away Keys;
let Observed=Actual() | project WindowEnd,ValueCount_Value,ValueCount_Other,Group=tostring(Group);
"""
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + query
        + "\n};\n"
        + expected
        + check("Observed", "Expected", ["WindowEnd", "Group", "ValueCount_Value", "ValueCount_Other"])
    )


def fixtures():
    from tests.fixtures.correlation.count_context import fixture as count_context_fixture

    return {
        "value_count_fractional.kql": fractional_fixture(),
        "value_count_real_intervals.kql": real_values_fixture(),
        "value_count_native_types.kql": native_types_fixture(),
        "value_count_grouped_counted_context.kql": count_context_fixture("value_count"),
    }


def native_types_fixture():
    populations = {
        "bool": "bool(false),bool(false),bool(true),bool(null)",
        "decimal": "decimal(9007199254740992.1),decimal(9007199254740992.1),decimal(9007199254740992.2),decimal(null)",
        "guid": "guid(00000000-0000-0000-0000-000000000001),guid(00000000-0000-0000-0000-000000000001),guid(00000000-0000-0000-0000-000000000002),guid(null)",
        "datetime": "datetime(2026-01-01),datetime(2026-01-01),datetime(2026-01-02),datetime(null)",
        "timespan": "0s,0s,1s,timespan(null)",
        "long": "9007199254740992,9007199254740992,9007199254740993,long(null)",
    }
    functions, calls = [], []
    for kind, population in populations.items():
        (query,) = convert_documents(
            [
                source_document(),
                correlation_document(
                    "count", "value_count", ["source-rule"], groups=(), field="Value", condition={"gte": 0}
                ),
            ],
            schema=fixture_schema(Value=kind),
        )
        functions.append(
            f"let {kind}Values=(){{\nlet Events=datatable(Value:{kind}) [{population}] "
            '| extend Timestamp=datetime(2026-01-01),Marker="match";\n' + query + "\n};\n"
        )
        calls.append(f'({kind}Values() | project Kind="{kind}",ValueCount)')
    expected = (
        "let Expected=datatable(Kind:string,ValueCount:long) ["
        + ",".join(f'"{kind}",2' for kind in populations)
        + "];\n"
    )
    return (
        HEADER
        + "".join(functions)
        + "let Actual=union "
        + ",".join(calls)
        + ";\n"
        + expected
        + check("Actual", "Expected", ["Kind", "ValueCount"])
    )


if __name__ == "__main__":
    from tests.fixtures.correlation.runtime import manifest

    destination = Path(__file__).parent
    for name, query in fixtures().items():
        (destination / name).write_text(query)
    prepared = manifest(destination)
    prepared["fixtures"] = [item for item in prepared["fixtures"] if item["name"] in fixtures()]
    (destination / "value-count-manifest.json").write_text(json.dumps(prepared, indent=2) + "\n")
