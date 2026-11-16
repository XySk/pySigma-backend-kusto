"""Supported-range sum cases with literal, independent result multisets."""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.generate import HEADER, check


def fixture(kind, data, expected, *, chain=False):
    child = correlation_document(
        "sum", "value_sum", ["source-rule"], groups=(), condition={"field": "Amount", "gte": 0}
    )
    docs = [source_document(), child]
    if chain:
        docs.append(
            correlation_document("parent", "value_sum", ["sum"], groups=(), condition={"field": "ValueSum", "gte": 0})
        )
    (query,) = convert_documents(docs, schema=fixture_schema(Amount=kind))
    return (
        HEADER
        + data
        + f"let Actual=(){{\n{query}\n}};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "ValueSum"])
    )


def fixtures():
    result = {}
    result["value_sum_real_expiration.kql"] = fixture(
        "real",
        """let Events=datatable(Timestamp:datetime,Amount:real,Marker:string) [
 datetime(2026-01-01T12:00:00Z),real(1e20),"match",
 datetime(2026-01-01T12:01:00Z),real(1),"match",
 datetime(2026-01-01T12:05:00.0000001Z),real(null),"match",
 datetime(2026-01-01T12:06:00.0000001Z),real(nan),"match",
 datetime(2026-01-01T12:07:00Z),real(+inf),"match",
 datetime(2026-01-01T12:08:00Z),real(-inf),"match"
];
""",
        """let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,ValueSum:real) [
 datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),real(1e20),
 datetime(2026-01-01T11:56:00Z),datetime(2026-01-01T12:01:00Z),real(1e20),
 datetime(2026-01-01T12:00:00.0000001Z),datetime(2026-01-01T12:05:00.0000001Z),real(1),
 datetime(2026-01-01T12:01:00.0000001Z),datetime(2026-01-01T12:06:00.0000001Z),real(0),
 datetime(2026-01-01T12:02:00Z),datetime(2026-01-01T12:07:00Z),real(0),
 datetime(2026-01-01T12:03:00Z),datetime(2026-01-01T12:08:00Z),real(0)
];
""",
    )
    result["value_sum_decimal_precision.kql"] = fixture(
        "decimal",
        """let Events=datatable(Timestamp:datetime,Amount:decimal,Marker:string) [
 datetime(2026-01-01T12:00:00Z),decimal(9007199254740993.125),"match",
 datetime(2026-01-01T12:01:00Z),decimal(0.25),"match",
 datetime(2026-01-01T12:06:00.0000001Z),decimal(null),"match"
];
""",
        """let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,ValueSum:decimal) [
 datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),decimal(9007199254740993.125),
 datetime(2026-01-01T11:56:00Z),datetime(2026-01-01T12:01:00Z),decimal(9007199254740993.375),
 datetime(2026-01-01T12:01:00.0000001Z),datetime(2026-01-01T12:06:00.0000001Z),decimal(0)
];
""",
    )
    result["value_sum_int_promotion.kql"] = fixture(
        "int",
        """let Events=datatable(Timestamp:datetime,Amount:int,Marker:string) [
 datetime(2026-01-01T12:00:00Z),2000000000,"match",
 datetime(2026-01-01T12:00:00Z),2000000000,"match"
];
""",
        """let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,ValueSum:long) [
 datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),4000000000
];
""",
        chain=True,
    )
    data = """let Events=datatable(Timestamp:datetime,Amount:long,Marker:string) [
 datetime(2026-01-01T12:00:00Z),2,"match",
 datetime(2026-01-01T12:06:00Z),long(null),"match"
];
"""
    calls = []
    for op in ("gt", "gte", "lt", "lte", "eq", "neq"):
        (query,) = convert_documents(
            [
                source_document(),
                correlation_document(
                    "sum", "value_sum", ["source-rule"], groups=(), condition={"field": "Amount", op: 2.5}
                ),
            ]
        )
        data += f"let Sum_{op}=(){{\n{query}\n}};\n"
        calls.append(f'(Sum_{op}() | extend Comparison="{op}")')
    data += "let Actual=union " + ",".join(calls) + ";\n"
    expected = "let Expected=datatable(Comparison:string,WindowStart:datetime,WindowEnd:datetime,ValueSum:long) [\n"
    expected += (
        ",\n".join(
            f'"{op}",datetime({start}),datetime({end}),{value}'
            for op in ("lt", "lte", "neq")
            for start, end, value in (
                ("2026-01-01T11:55:00Z", "2026-01-01T12:00:00Z", 2),
                ("2026-01-01T12:01:00Z", "2026-01-01T12:06:00Z", 0),
            )
        )
        + "\n];\n"
    )
    result["value_sum_fractional.kql"] = (
        HEADER + data + expected + check("Actual", "Expected", ["Comparison", "WindowStart", "WindowEnd", "ValueSum"])
    )
    for kind in ("long", "real"):
        result[f"value_sum_real_groups_{kind}.kql"] = real_groups_fixture(kind)
    return result


def real_groups_fixture(kind):
    (query,) = convert_documents(
        [
            source_document(),
            correlation_document(
                "sum", "value_sum", ["source-rule"], groups=("Group",), condition={"field": "Amount", "gte": 0}
            ),
        ],
        schema=fixture_schema(Group="real", Amount=kind),
    )
    data = f"""let Events=datatable(Timestamp:datetime,Group:real,Amount:{kind},Marker:string) [
 datetime(2026-01-01T00:00:00),real(nan),2,"match",
 datetime(2026-01-01T00:01:00),real(nan),3,"match",
 datetime(2026-01-01T00:00:00),real(+inf),2,"match",
 datetime(2026-01-01T00:01:00),real(+inf),3,"match",
 datetime(2026-01-01T00:00:00),real(-inf),2,"match",
 datetime(2026-01-01T00:01:00),real(-inf),3,"match",
 datetime(2026-01-01T00:00:00),real(-0.0),2,"match",
 datetime(2026-01-01T00:01:00),real(+0.0),3,"match"
];
"""
    expected = """let Expected=datatable(WindowEnd:datetime,ValueSum:real) [
 datetime(2026-01-01T00:00:00),2,datetime(2026-01-01T00:01:00),5
]
| extend Keys=dynamic(["NaN","inf","-inf","0.0"])
| mv-expand Group=Keys to typeof(string)
| project WindowEnd,ValueSum,Group;
let Observed=Actual() | project WindowEnd,ValueSum=toreal(ValueSum),Group=tostring(Group);
union (Expected | extend Delta=-1),(Observed | extend Delta=1)
| summarize Delta=sum(Delta) by WindowEnd,ValueSum,Group
| where Delta!=0
"""
    return HEADER + data + "let Actual=(){\n" + query + "\n};\n" + expected
