"""Generated public-path average queries with independently worked expected rows."""

from sigma.pipelines.kusto_common.schema import FieldMappings
from tests.correlation_helpers import convert_documents, correlation_document, source_document


def average_fixtures(check, header):
    header = header.replace(
        "Prepared deterministic fixture; live engine execution has not been performed.",
        "Deterministic fixture; see RUNTIME.md for recorded engine execution.",
    )

    def converted(operator, threshold, **kwargs):
        return convert_documents(
            [
                source_document(),
                correlation_document(
                    "average",
                    "value_avg",
                    ["source-rule"],
                    condition={"field": "Amount", operator: threshold},
                    **kwargs,
                ),
            ]
        )[0]

    # Means at the four anchors: 2.5, 5/3, 2, 1.5. Empty-only
    # groups never qualify, including for neq. Every input row is an anchor.
    data = """let Events = datatable(Timestamp:string, Host:string, Amount:dynamic, Marker:string) [
    "2026-01-01T12:00:00Z", "mixed", dynamic(8), "match",
    "2026-01-01T12:00:00Z", "mixed", dynamic(-3), "match",
    "2026-01-01T12:01:00Z", "mixed", dynamic(0), "match",
    "2026-01-01T12:05:00Z", "mixed", dynamic(3), "match",
    "2026-01-01T12:05:00.0000001Z", "mixed", dynamic("bad"), "match",
    "2026-01-01T12:00:00Z", "empty", dynamic("7"), "match",
    "2026-01-01T12:01:00Z", "empty", dynamic(null), "match",
    "2026-01-01T12:02:00Z", "mixed", dynamic(100), "other",
    "invalid", "mixed", dynamic(100), "match",
    "2026-01-01T12:00:00Z", "", dynamic(100), "match"
];
"""
    scenarios = [
        ("gt", 2.5, []),
        ("gte", 2.5, [(0, 2.5)]),
        ("lt", 2.5, [(1, 1.6666666666666667), (2, 2), (3, 1.5)]),
        ("lte", 2.5, [(0, 2.5), (1, 1.6666666666666667), (2, 2), (3, 1.5)]),
        ("eq", 2.5, [(0, 2.5)]),
        ("neq", 2.5, [(1, 1.6666666666666667), (2, 2), (3, 1.5)]),
        ("gt", 1.75, [(0, 2.5), (2, 2)]),
    ]
    times = [
        ("11:55:00", "12:00:00"),
        ("11:56:00", "12:01:00"),
        ("12:00:00", "12:05:00"),
        ("12:00:00.0000001", "12:05:00.0000001"),
    ]
    functions, calls, rows = [], [], []
    for index, (operator, threshold, results) in enumerate(scenarios):
        functions.append(f"let average_{index} = () {{\n{converted(operator, threshold)}\n}};\n")
        calls.append(f'(average_{index}() | extend Scenario = "{index}")')
        for anchor, value in results:
            start, end = times[anchor]
            rows.append(f'"{index}", datetime(2026-01-01T{start}Z), datetime(2026-01-01T{end}Z), "mixed", {value}')
    comparisons = (
        header
        + data
        + "".join(functions)
        + "let Actual = union "
        + ", ".join(calls)
        + ";\n"
        + "let Expected = datatable(Scenario:string, WindowStart:datetime, WindowEnd:datetime, Host:string, ValueAvg:real) [\n"
        + ",\n".join(rows)
        + "\n];\n"
        + check("Actual", "Expected", ["Scenario", "WindowStart", "WindowEnd", "Host", "ValueAvg"])
    )

    reals_data = """let Events = datatable(Timestamp:datetime, Host:string, Amount:dynamic, Marker:string) [
    datetime(2026-01-01T12:00:00Z), "finite", dynamic(0), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic(-2.0), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic(3.5), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic("100"), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic("nan_value"), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic("positive_inf"), "match",
    datetime(2026-01-01T12:00:00Z), "finite", dynamic("negative_inf"), "match",
    datetime(2026-01-01T12:00:00Z), "undefined", dynamic("nan_value"), "match",
    datetime(2026-01-01T12:00:00Z), "undefined", dynamic("positive_inf"), "match",
    datetime(2026-01-01T12:00:00Z), "undefined", dynamic(null), "match",
    datetime(2026-01-01T12:00:00Z), "float", dynamic(0.1), "match",
    datetime(2026-01-01T12:00:00Z), "float", dynamic(0.2), "match",
    datetime(2026-01-01T12:01:00Z), "undefined", dynamic("bad"), "match"
]
| extend Amount = case(tostring(Amount) == "nan_value", pack_array(real(nan))[0],
    tostring(Amount) == "positive_inf", pack_array(real(+inf))[0],
    tostring(Amount) == "negative_inf", pack_array(real(-inf))[0], Amount);
"""
    expected = """let Expected = datatable(WindowStart:datetime, WindowEnd:datetime, Host:string, ValueAvg:real) [
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "finite", 0.5,
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "float", 0.15000000000000002
];
"""
    reals = (
        header
        + reals_data
        + f"let Actual = () {{\n{converted('neq', 0.15)}\n}};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValueAvg"])
    )

    documents = [
        source_document("child-source", "child"),
        source_document("direct-source", "direct"),
        correlation_document("child", "value_avg", ["child-source"], condition={"field": "Amount", "gte": 0}),
        correlation_document(
            "parent",
            "value_avg",
            ["child", "direct-source"],
            condition={"field": "Measured", "gte": 0},
            aliases={"Measured": {"child": "ValueAvg", "direct-source": "Amount"}},
        ),
    ]
    query = convert_documents(documents)[0]
    chain_data = """// Child means 2 and 4 contribute once, alongside direct 9: parent 2 then 5.
let Events = datatable(Timestamp:datetime, Host:string, Amount:dynamic, Marker:string) [
    datetime(2026-01-01T12:00:00Z), "h", dynamic(2), "child",
    datetime(2026-01-01T12:01:00Z), "h", dynamic(6), "child",
    datetime(2026-01-01T12:01:00Z), "h", dynamic(9), "direct"
];
"""
    expected = """let Expected = datatable(WindowStart:datetime, WindowEnd:datetime, Host:string, ValueAvg:real) [
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "h", 2.0,
    datetime(2026-01-01T11:56:00Z), datetime(2026-01-01T12:01:00Z), "h", 5.0
];
"""
    chained = (
        header
        + chain_data
        + f"let Actual = () {{\n{query}\n}};\n"
        + expected
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValueAvg"])
    )

    # Four matching searches contribute repeated 2s and one other search 12:
    # (2 + 2 + 2 + 2 + 12) / 5 = 4; deduplicating would produce 7.
    first = source_document("first")
    first["detection"]["alternate"] = first["detection"]["selection"]
    first["detection"]["condition"] = ["selection", "alternate"]
    second = {**first, "name": "second"}
    third = source_document("third", "other")
    query = convert_documents(
        [
            first,
            second,
            third,
            correlation_document(
                "average", "value_avg", ["first", "second", "third"], groups=(), condition={"field": "Amount", "eq": 4}
            ),
        ]
    )[0]
    data = """let Events = datatable(Timestamp:datetime, Amount:dynamic, Marker:string) [
    datetime(2026-01-01T12:00:00Z), dynamic(2), "match",
    datetime(2026-01-01T12:00:00Z), dynamic(12), "other"
];
let Expected = datatable(WindowStart:datetime, WindowEnd:datetime, ValueAvg:real) [
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), 4.0
];
"""
    duplicates = (
        header
        + data
        + f"let Actual = () {{\n{query}\n}};\n"
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "ValueAvg"])
    )
    documents = [
        source_document("a"),
        source_document("b", product="network"),
        correlation_document(
            "average",
            "value_avg",
            ["a", "b"],
            groups=("Machine",),
            condition={"field": "Measurement", "gte": 0},
            aliases={"Measurement": {"a": "Amount", "b": "Bytes"}, "Machine": {"a": "Host", "b": "Device"}},
        ),
    ]
    mappings = FieldMappings(
        table_mappings={
            "Events": {"Timestamp": "ProcessTime", "Amount": "Size", "Host": "Computer"},
            "NetworkEvents": {"Timestamp": "NetworkTime", "Bytes": "Length", "Device": "Server"},
        }
    )
    query = convert_documents(documents, mappings=mappings)[0]
    data = """let Events = datatable(ProcessTime:datetime, Computer:string, Size:long, Marker:string) [
    datetime(2026-01-01T12:00:00Z), "h", 2, "match",
    datetime(2026-01-01T12:00:00Z), "h", 2, "match"
];
let NetworkEvents = datatable(NetworkTime:datetime, Server:string, Length:long, Marker:string) [
    datetime(2026-01-01T12:00:00Z), "h", 8, "match",
    datetime(2026-01-01T12:05:00Z), "h", 14, "match"
];
let Expected = datatable(WindowStart:datetime, WindowEnd:datetime, Machine:string, ValueAvg:real) [
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "h", 4.0,
    datetime(2026-01-01T12:00:00Z), datetime(2026-01-01T12:05:00Z), "h", 6.5
];
"""
    mapped = (
        header
        + data
        + f"let Actual = () {{\n{query}\n}};\n"
        # Grouping types are covered by typed-grouping fixtures; this legacy
        # diagnostic compares the mapped string literals with the same schema.
        + check(
            "Actual() | extend Machine=tostring(Machine)",
            "Expected",
            ["WindowStart", "WindowEnd", "Machine", "ValueAvg"],
        )
    )
    query = converted("gte", -1)
    data = """let Events = datatable(Timestamp:datetime, Host:string, Amount:long, Marker:string) [
    datetime(2026-01-01T12:00:00Z), "maximum", 9223372036854775807, "match",
    datetime(2026-01-01T12:00:00Z), "maximum", 9223372036854775807, "match",
    datetime(2026-01-01T12:00:00Z), "cancel", 9223372036854775807, "match",
    datetime(2026-01-01T12:00:00Z), "cancel", -9223372036854775807, "match"
];
let Expected = datatable(WindowStart:datetime, WindowEnd:datetime, Host:string, ValueAvg:real) [
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "maximum", 9.223372036854776e18,
    datetime(2026-01-01T11:55:00Z), datetime(2026-01-01T12:00:00Z), "cancel", 0.0
];
"""
    bounds = (
        header
        + data
        + f"let Actual = () {{\n{query}\n}};\n"
        + check("Actual()", "Expected", ["WindowStart", "WindowEnd", "Host", "ValueAvg"])
    )
    return {
        "value_avg_mapped.kql": mapped,
        "value_avg_integer_bounds.kql": bounds,
        "value_avg_comparisons.kql": comparisons,
        "value_avg_reals.kql": reals,
        "value_avg_chained.kql": chained,
        "value_avg_duplicate_sources.kql": duplicates,
    }
