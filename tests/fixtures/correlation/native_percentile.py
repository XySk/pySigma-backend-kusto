"""Exact native percentile examples, with literal independent expected values.

The threshold-count alternative exists only for strategy measurement; generated
backend output always retains the selected ValuePercentile metric.
"""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.generate import HEADER, check

OPERATORS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "==", "neq": "!="}


def query(selector=50, operator="gte", threshold=0, *, kind="long", groups=(), documents=None):
    documents = documents or [
        source_document(),
        correlation_document(
            "percentile",
            "value_percentile",
            ["source-rule"],
            groups=groups,
            condition={"field": "Amount", "percentile": selector, operator: threshold},
        ),
    ]
    return convert_documents(documents, schema=fixture_schema(Amount=kind, Group="real"))[0]


def exact_prefilter(query, selector, operator, threshold):
    """Attach exact threshold counts before selection, preserving its metric.

    For rank r, the selected value is below x iff at least r values are below
    x, and at most x iff at least r values are at most x. These two counts cover
    all six comparisons without approximating percentile values.
    """
    rank = (
        f"max_of(1, (PercentilePopulation / 100) * {selector} + "
        f"tolong(ceiling(toreal((PercentilePopulation % 100) * {selector}) / 100.0)))"
    )
    comparison = f"todecimal(MeasuredAmount) {{}} decimal({threshold})"
    summary = "PercentilePopulation = count() by "
    query = query.replace(
        summary,
        "PercentilePopulation = count(), "
        f"BelowThreshold = countif({comparison.format('<')}), "
        f"AtMostThreshold = countif({comparison.format('<=')}) by ",
    )
    predicate = {
        "gt": f"AtMostThreshold < {rank}",
        "gte": f"BelowThreshold < {rank}",
        "lt": f"BelowThreshold >= {rank}",
        "lte": f"AtMostThreshold >= {rank}",
        "eq": f"BelowThreshold < {rank} and AtMostThreshold >= {rank}",
        "neq": f"BelowThreshold >= {rank} or AtMostThreshold < {rank}",
    }[operator]
    return query.replace(
        "\n| project PercentileWindowEnd, MeasuredAmount, PercentilePopulation",
        f"\n| where {predicate}\n| project PercentileWindowEnd, MeasuredAmount, PercentilePopulation",
    ).replace(
        "\n| project GroupHost, PercentileWindowEnd, MeasuredAmount, PercentilePopulation",
        f"\n| where {predicate}\n| project GroupHost, PercentileWindowEnd, MeasuredAmount, PercentilePopulation",
    )


def comparisons_fixture():
    data = """let Events=datatable(Timestamp:datetime,Amount:long,Marker:string) [
 datetime(2026-01-01T12:00:00Z),1,"match",
 datetime(2026-01-01T12:00:00Z),2,"match",
 datetime(2026-01-01T12:00:00Z),100,"match",
 datetime(2026-01-01T12:00:00Z),200,"match",
 datetime(2026-01-01T12:05:00Z),long(null),"match",
 datetime(2026-01-01T12:05:00.0000001Z),long(null),"match"
];
"""
    calls, expected = [], []
    # The literal nearest ranks of [1,2,100,200] are 1,1,2,200,200.
    for selector, selected in ((0, 1), (1, 1), (50, 2), (99, 200), (100, 200)):
        # Literal qualifying comparisons against 2.5, independent of KQL.
        qualifying = ("lt", "lte", "neq") if selected < 2.5 else ("gt", "gte", "neq")
        for operator in OPERATORS:
            name = f"P{selector}_{operator}"
            text = query(selector, operator, 2.5)
            data += f"let {name}=(){{\n{text}\n}};\n"
            calls.append(f'({name}() | extend Case="{name}")')
            if operator in qualifying:
                for start, end in (("11:55:00", "12:00:00"), ("12:00:00", "12:05:00")):
                    expected.append(f'"{name}",datetime(2026-01-01T{start}Z),datetime(2026-01-01T{end}Z),{selected}')
            if selector == 50:
                name += "_prefilter"
                data += f"let {name}=(){{\n{exact_prefilter(text, selector, operator, 2.5)}\n}};\n"
                calls.append(f'({name}() | extend Case="{name}")')
                if operator in qualifying:
                    for start, end in (("11:55:00", "12:00:00"), ("12:00:00", "12:05:00")):
                        expected.append(f'"{name}",datetime(2026-01-01T{start}Z),datetime(2026-01-01T{end}Z),2')
    data += "let Actual=union " + ",".join(calls) + ";\n"
    data += "let Expected=datatable(Case:string,WindowStart:datetime,WindowEnd:datetime,ValuePercentile:long)[\n"
    data += ",\n".join(expected) + "\n];\n"
    return HEADER + data + check("Actual", "Expected", ["Case", "WindowStart", "WindowEnd", "ValuePercentile"])


def values_fixture(kind, values, selected):
    data = f"let Events=datatable(Timestamp:datetime,Amount:{kind},Marker:string)[\n"
    data += ",\n".join(f'datetime(2026-01-01T12:00:00Z),{value},"match"' for value in values) + "\n];\n"
    expected = (
        f"let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,ValuePercentile:{kind})["
        f"datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),{selected}];\n"
    )
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + query(kind=kind)
        + "\n};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "ValuePercentile"])
    )


def strategy_fixture(prefilter, threshold):
    text = query(groups=("Host",), threshold=threshold)
    if prefilter:
        text = exact_prefilter(text, 50, "gte", threshold)
    data = """let Events=range GroupId from 0 to 99 step 1
| extend Slots=range(0,19) | mv-expand Slot=Slots to typeof(long)
| project Timestamp=datetime(2026-01-01)+Slot*1s,Host=tostring(GroupId),Amount=GroupId,Marker="match";
"""
    expected = f"""let Expected=Events | where Amount >= {threshold}
| project WindowStart=Timestamp-5m,WindowEnd=Timestamp,Host,ValuePercentile=Amount;
"""
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + text
        + "\n};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValuePercentile"])
    )


def fixtures():
    return {
        "value_percentile_native_comparisons.kql": comparisons_fixture(),
        "value_percentile_native_ties.kql": ties_fixture(),
        "value_percentile_native_int.kql": values_fixture("int", ["1", "2", "100", "200"], "2"),
        "value_percentile_native_mixed_context.kql": mixed_context_fixture(),
        "value_percentile_native_real_groups.kql": real_groups_fixture(),
        "value_percentile_native_long.kql": values_fixture(
            "long",
            ["9007199254740993", "9007199254740992", "9007199254740994", "9007199254740995"],
            "9007199254740993",
        ),
        "value_percentile_native_decimal.kql": values_fixture(
            "decimal",
            [
                "decimal(9007199254740993.125)",
                "decimal(9007199254740993.25)",
                "decimal(9007199254740993.5)",
                "decimal(9007199254740993.75)",
            ],
            "decimal(9007199254740993.25)",
        ),
        "value_percentile_native_real.kql": values_fixture(
            "real",
            ["real(0)", "real(0)", "real(3)", "real(nan)", "real(+inf)", "real(-inf)", "real(null)"],
            "real(0)",
        ),
    }


def mixed_context_fixture():
    source = source_document()
    source["fields"] = ["Amount"]
    child = correlation_document("child", "event_count", ["source-rule"], condition={"gte": 1})
    parent = correlation_document(
        "parent",
        "value_percentile",
        ["child", "direct"],
        condition={"field": "Measurement", "percentile": 50, "gte": 0},
        aliases={"Measurement": {"child": "Amount", "direct": "Size"}},
    )
    text = query(documents=[parent, source_document("direct", "direct"), child, source])
    data = """let Events=datatable(Timestamp:datetime,Host:string,Amount:long,Size:long,Marker:string)[
 datetime(2026-01-01T12:00:00Z),"h",1,long(null),"match",
 datetime(2026-01-01T12:00:00Z),"h",1,long(null),"match",
 datetime(2026-01-01T12:00:00Z),"h",2,long(null),"match",
 datetime(2026-01-01T12:00:00Z),"h",100,long(null),"match",
 datetime(2026-01-01T12:00:00Z),"h",200,long(null),"match",
 datetime(2026-01-01T12:01:00Z),"h",long(null),0,"direct",
 datetime(2026-01-01T12:01:00Z),"h",long(null),0,"direct"
];
"""
    expected = """let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,Host:string,ValuePercentile:long)[
 datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),"h",2,
 datetime(2026-01-01T11:56:00Z),datetime(2026-01-01T12:01:00Z),"h",1
];
"""
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + text
        + "\n};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValuePercentile"])
    )


def real_groups_fixture():
    data = """let Events=datatable(Timestamp:datetime,Group:real,Amount:long,Marker:string)[
 datetime(2026-01-01T12:00:00Z),real(nan),1,"match",
 datetime(2026-01-01T12:00:00Z),real(nan),2,"match",
 datetime(2026-01-01T12:00:00Z),real(nan),100,"match",
 datetime(2026-01-01T12:00:00Z),real(nan),200,"match",
 datetime(2026-01-01T12:00:00Z),real(+inf),1,"match",
 datetime(2026-01-01T12:00:00Z),real(+inf),2,"match",
 datetime(2026-01-01T12:00:00Z),real(+inf),100,"match",
 datetime(2026-01-01T12:00:00Z),real(+inf),200,"match",
 datetime(2026-01-01T12:00:00Z),real(-inf),1,"match",
 datetime(2026-01-01T12:00:00Z),real(-inf),2,"match",
 datetime(2026-01-01T12:00:00Z),real(-inf),100,"match",
 datetime(2026-01-01T12:00:00Z),real(-inf),200,"match",
 datetime(2026-01-01T12:00:00Z),real(-0.0),1,"match",
 datetime(2026-01-01T12:00:00Z),real(+0.0),2,"match",
 datetime(2026-01-01T12:00:00Z),real(-0.0),100,"match",
 datetime(2026-01-01T12:00:00Z),real(+0.0),200,"match"
];
"""
    expected = """let Expected=datatable(Group:string)["NaN","inf","-inf","0.0"]
| extend WindowStart=datetime(2026-01-01T11:55:00Z),WindowEnd=datetime(2026-01-01T12:00:00Z),ValuePercentile=long(2);
let Observed=Actual() | extend Group=tostring(Group);
"""
    return (
        HEADER
        + data
        + "let Actual=(){\n"
        + query(groups=("Group",))
        + "\n};\n"
        + expected
        + check("Observed", "Expected", ["WindowStart", "WindowEnd", "Group", "ValuePercentile"])
    )


def ties_fixture():
    data = """let Events=datatable(Timestamp:datetime,Amount:long,Marker:string)[
 datetime(2026-01-01T12:00:00Z),0,"match",
 datetime(2026-01-01T12:00:00Z),0,"match",
 datetime(2026-01-01T12:00:00Z),3,"match",
 datetime(2026-01-01T12:05:00Z),long(null),"match",
 datetime(2026-01-01T12:05:00.0000001Z),long(null),"match"
];
"""
    calls, rows = [], []
    for operator in OPERATORS:
        for prefilter in (False, True):
            name = f"Ties_{operator}_{prefilter}"
            text = query(operator=operator)
            if prefilter:
                text = exact_prefilter(text, 50, operator, 0)
            data += f"let {name}=(){{\n{text}\n}};\n"
            calls.append(f'({name}() | extend Case="{name}")')
            if operator in ("gte", "lte", "eq"):
                for start, end in (("11:55:00", "12:00:00"), ("12:00:00", "12:05:00")):
                    rows.append(f'"{name}",datetime(2026-01-01T{start}Z),datetime(2026-01-01T{end}Z),0')
    data += "let Actual=union " + ",".join(calls) + ";\n"
    data += "let Expected=datatable(Case:string,WindowStart:datetime,WindowEnd:datetime,ValuePercentile:long)[\n"
    data += ",\n".join(rows) + "\n];\n"
    return HEADER + data + check("Actual", "Expected", ["Case", "WindowStart", "WindowEnd", "ValuePercentile"])
