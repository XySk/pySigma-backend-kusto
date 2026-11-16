"""Independent scalar context diagnostics through ordinary public conversion."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def _query(parent=None, *, limit=None, callback=None):
    child = correlation_document("child", "event_count", ["source-rule"], groups=("Host",), condition={"gte": 1})
    child["fields"] = ["User", "Server", "Amount"]
    documents = [source_document(), child]
    if parent:
        documents.append(parent)
    return convert_documents(
        documents, backend_options={} if limit is None else {"correlation_context_limit": limit}, callback=callback
    )[0]


def display(size, limit=1000):
    query = _query(limit=None if limit == 1000 else limit)
    return f"""let Events = range Index from 1 to {size} step 1
| project Timestamp=datetime(2026-01-01), Host="h", Marker="match", User=tostring(Index), Server=strcat("s", Index), Amount=Index;
let Actual = () {{
{query}
}};
Actual()
| project WindowEnd, EventCount, Users=array_length(User), Servers=array_length(Server), Amounts=array_length(Amount), ContextComplete
| where WindowEnd != datetime(2026-01-01) or EventCount != {size} or Users != {min(size,limit)} or Servers != {min(size,limit)} or Amounts != {min(size,limit)} or ContextComplete != {str(size<=limit).lower()}
| union (Actual() | summarize Rows=count() | where Rows != 1 | project WindowEnd=datetime(null),EventCount=Rows,Users=long(null),Servers=long(null),Amounts=long(null),ContextComplete=false)
"""


def complete_parent():
    parent = correlation_document("parent", "event_count", ["child"], groups=("User", "Server"), condition={"gte": 1})
    query = _query(parent, limit=2)
    return f"""let Events = range Index from 1 to 1001 step 1
| project Timestamp=datetime(2026-01-01), Host="h", Marker="match", User=tostring(Index), Server=strcat("s", Index), Amount=Index;
let Actual = () {{
{query}
}};
let Observed=Actual() | project User,Server,EventCount;
let Expected=range Index from 1 to 1001 step 1 | project User=tostring(Index),Server=strcat("s",Index),EventCount=long(1);
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by User,Server,EventCount | where Delta != 0
"""


def dedup(kind="value_sum", *, metric=False, groups=("User",), callback=None):
    field = "EventCount" if metric else "Amount"
    parent = correlation_document(
        "parent", kind, ["child"], groups=groups, condition={"field": field, "gte": 0}, field=field
    )
    if kind == "value_avg":
        expected = "3.0" if metric else "1.5"
        result = "ValueAvg"
    elif kind == "value_percentile":
        expected = "3" if metric else "1"
        result = "ValuePercentile"
    elif kind == "event_count":
        expected = "1"
        result = "EventCount"
    else:
        expected = "3"
        result = "ValueSum"
    query = _query(parent, limit=1, callback=callback)
    if callback:
        expected = "13"
    return f"""let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:string,Server:string,Amount:long) [
 datetime(2026-01-01),"h","match","u","s",1,
 datetime(2026-01-01),"h","match","u","s",1,
 datetime(2026-01-01),"h","match","u","s",2
];
let Actual=() {{
{query}
}};
let Observed=Actual() | project WindowEnd,Metric=toreal({result});
let Expected=datatable(WindowEnd:datetime,Metric:real) [datetime(2026-01-01),{expected}];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Metric | where Delta != 0
"""


def fixtures():
    output = {f"relational_display_{size}.kql": display(size) for size in (999, 1000, 1001)}
    output["relational_display_configured.kql"] = display(3, 2)
    output["relational_complete_parent.kql"] = complete_parent()
    for kind in ("event_count", "value_sum", "value_avg", "value_percentile"):
        output[f"relational_{kind}_dedup.kql"] = dedup(kind)
        if kind != "event_count":
            output[f"relational_{kind}_metric.kql"] = dedup(kind, metric=True)
    output["relational_ungrouped_count.kql"] = dedup("event_count", groups=())

    def update_metric(rule, output_format, index, condition, result):
        return (
            result + "\n| where EventCount > 0\n| extend EventCount = EventCount + 10"
            if rule.name == "child"
            else result
        )

    output["relational_callback_metric.kql"] = dedup(metric=True, callback=update_metric)
    from tests.fixtures.correlation.boolean_context import fixtures as boolean_fixtures
    from tests.fixtures.correlation.ordered_context import fixture as ordered_fixture
    from tests.fixtures.correlation.temporal_context import fixture as temporal_fixture

    output["relational_temporal_shared.kql"] = temporal_fixture("temporal", multilevel=True, shared=True)
    output["relational_ordered_multilevel.kql"] = temporal_fixture("temporal_ordered", multilevel=True)
    output["relational_boolean_multilevel.kql"] = temporal_fixture("boolean", multilevel=True)
    output["relational_ordered_alternatives.kql"] = ordered_fixture()
    output["relational_boolean_alternatives.kql"] = boolean_fixtures()["context_boolean_alternatives.kql"]
    return output
