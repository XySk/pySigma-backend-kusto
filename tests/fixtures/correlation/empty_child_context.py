"""Absence-only child matches anchor parents without inventing retained context."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def documents(kind, groups=(), operator="eq"):
    sources = [source_document(name, name) for name in ("A", "B")]
    for source in sources:
        source["fields"] = ["Amount"]
    child = correlation_document("child", "temporal", [], groups=("Host",) if groups == ("Host",) else ())
    child["correlation"]["condition"] = "not A or not B"
    # The expression supplies references through the standard Boolean parser.
    child["correlation"].pop("rules")
    parent = correlation_document(
        "parent", kind, ["child"], groups=groups, condition={"field": "Amount", operator: 0}, field="Amount"
    )
    if groups == ("ChildRuleCount",):
        parent["correlation"]["aliases"] = {"ChildRuleCount": {"child": "RuleCount"}}
    parent["fields"] = ["Amount"]
    return [*sources, child, parent]


def fixture(kind, groups=(), operator="eq"):
    (query,) = convert_documents(documents(kind, groups, operator))
    metric = {
        "value_sum": "ValueSum",
        "value_count": "ValueCount",
        "value_avg": "ValueAvg",
        "value_percentile": "ValuePercentile",
    }[kind]
    # The A row establishes a child match via `not B`, with no positive contributors.
    # Sum and distinct count are zero; undefined average/percentile compare false.
    matches = kind in ("value_sum", "value_count") and groups != ("Amount",) and operator == "eq"
    scalar_group = groups in (("Host",), ("ChildRuleCount",))
    group_projection = f", Group=tostring({groups[0]})" if scalar_group else ""
    group_schema = ", Group:string" if scalar_group else ""
    group_value = (', "h"' if groups == ("Host",) else ', "1"') if scalar_group else ""
    expected = ("datetime(2026-01-01), 0, 0, true" + group_value) if matches else ""
    values = "long(0)" if groups == ("Amount",) else "array_length(Amount)"
    keys = "WindowEnd, Metric, Values, ContextComplete" + (", Group" if scalar_group else "")
    return (
        "let Events=datatable(Timestamp:datetime, Host:string, Marker:string, Amount:long) "
        '[datetime(2026-01-01), "h", "A", 42];\nlet Actual=(){\n'
        + query
        + "\n};\n"
        + f"let Observed=Actual() | project WindowEnd, Metric=toreal({metric}), "
        f"Values={values}, ContextComplete"
        + group_projection
        + ";\n"
        + f"let Expected=datatable(WindowEnd:datetime, Metric:real, Values:long, ContextComplete:bool{group_schema}) [{expected}];\n"
        + "union (Observed | extend Delta=1), (Expected | extend Delta=-1)\n"
        + f"| summarize Delta=sum(Delta) by {keys}\n| where Delta!=0\n"
    )


def escaped_detection_fixture():
    import yaml

    from sigma.backends.kusto import KustoBackend
    from sigma.collection import SigmaCollection
    from tests.test_standard_mapping_kusto import mapping_pipeline

    source = source_document()
    source["detection"]["selection"] = {"User": ["fixed", "*suffix"]}
    (predicate,) = KustoBackend(processing_pipeline=mapping_pipeline({"User": "Field Space"})).convert(
        SigmaCollection.from_yaml(yaml.safe_dump(source))
    )
    return (
        'let Events=datatable(["Field Space"]:string) ["fixed", "a suffix", "ignored"];\n'
        + "let Observed=Events | where "
        + predicate
        + ' | project Value=["Field Space"];\n'
        + 'let Expected=datatable(Value:string) ["fixed", "a suffix"];\n'
        + "union (Observed | extend Delta=1), (Expected | extend Delta=-1)\n"
        + "| summarize Delta=sum(Delta) by Value\n| where Delta!=0\n"
    )


def fixtures():
    return {
        "escaped_mixed_detection.kql": escaped_detection_fixture(),
        **{
            f'empty_child_{kind}_{group or "ungrouped"}_eq.kql': fixture(kind, (group,) if group else ())
            for kind in ("value_sum", "value_count")
            for group in ("", "ChildRuleCount", "Amount")
        },
    }
