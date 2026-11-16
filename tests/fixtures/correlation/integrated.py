"""Seven-by-seven producer/consumer acceptance through public collection parsing.

One observation at noon qualifies every child, retaining Amount=[2.5]. Every
parent therefore sees one child and one numeric value. The independently worked
parent result ends at noon and begins at 11:55, regardless of the child's 1m span.
These small examples complement the grouped/mixed/multilevel stage fixtures.
"""

from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document

TYPES = ("event_count", "value_count", "value_sum", "value_avg", "value_percentile", "temporal", "temporal_ordered")
METRICS = {
    "event_count": "EventCount",
    "value_count": "ValueCount",
    "value_sum": "ValueSum",
    "value_avg": "ValueAvg",
    "value_percentile": "ValuePercentile",
    "temporal": "RuleCount",
    "temporal_ordered": "RuleCount",
}
# Literal worked results, independent of backend query construction.
EXPECTED = {
    "event_count": 1,
    "value_count": 1,
    "value_sum": 2.5,
    "value_avg": 2.5,
    "value_percentile": 2.5,
    "temporal": 1,
    "temporal_ordered": 1,
}


def documents(producer, consumer):
    source = source_document()
    source["fields"] = ["Amount"]

    def rule(name, kind, refs, span):
        condition = {"gte": 1}
        if kind.startswith("value_"):
            condition["field"] = "Amount"
        return correlation_document(name, kind, refs, groups=(), span=span, condition=condition, field="Amount")

    return [source, rule("child", producer, ["source-rule"], "1m"), rule("parent", consumer, ["child"], "5m")]


def matrix_fixture(producer, consumer):
    (query,) = convert_documents(documents(producer, consumer), schema=fixture_schema(Amount="real"))
    return (
        "// Prepared acceptance fixture; runtime PENDING.\n"
        "let Events=datatable(Timestamp:datetime,Marker:string,Amount:real) [\n"
        ' datetime(2026-01-01T12:00:00Z),"match",2.5\n];\nlet Actual=(){\n'
        + query
        + f"""
}};
let Observed=Actual() | project WindowStart,WindowEnd,Metric=toreal({METRICS[consumer]});
let Expected=datatable(WindowStart:datetime,WindowEnd:datetime,Metric:real) [
 datetime(2026-01-01T11:55:00Z),datetime(2026-01-01T12:00:00Z),{EXPECTED[consumer]}
];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowStart,WindowEnd,Metric
| where Delta!=0
"""
    )


def matrix_fixtures():
    return {
        f"matrix_{producer}__{consumer}.kql": matrix_fixture(producer, consumer)
        for producer in TYPES
        for consumer in TYPES
    }
