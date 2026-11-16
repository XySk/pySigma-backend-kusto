"""Numeric child-context examples with independently worked literal result rows."""

from tests.correlation_helpers import convert_documents, correlation_document, numeric_documents, source_document

DATA = '''let Events=datatable(Timestamp:datetime,Host:string,Marker:string,User:string,Amount:dynamic) [
 datetime(2026-01-01T12:00:00Z),"h","match","Alice",dynamic(10),
 datetime(2026-01-01T12:00:00Z),"h","match","Alice",dynamic(10),
 datetime(2026-01-01T12:00:00Z),"h","match","Alice",dynamic(20),
 datetime(2026-01-01T12:00:00Z),"h","match","Bob",dynamic(100),
 datetime(2026-01-01T12:00:00Z),"h2","match","Alice",dynamic(10),
 datetime(2026-01-01T12:00:00Z),"d","direct","Alice",dynamic(5)
];
'''


def fixture(kind, *, grouped=False, mixed=False, metric=False, nested=False, boundary=False, empty=False):
    docs = numeric_documents(kind, ("User",) if grouped else (), "EventCount" if metric else "Amount")
    docs[-1]["correlation"]["condition"]["gte"] = -1
    if empty:
        docs[-1]["correlation"]["condition"] = {"field": "Amount", "neq": -1}
    if kind == "value_percentile":
        docs[-1]["correlation"]["condition"]["percentile"] = 60
    if mixed:
        docs.insert(-1, source_document("direct", "direct"))
        docs[-1]["correlation"]["rules"].append("direct")
    if nested:
        docs[0]["fields"] = ["Amount"]
        docs[-1]["fields"] = ["Amount"]
        docs.append(correlation_document("grandparent", kind, ["parent"], groups=(),
                                         condition={"field": "Amount", "gte": -1}))
    if grouped:
        # Exercise logical aliases on child fields and direct sources together.
        parent = docs[-1]["correlation"]
        parent["group-by"] = ["Actor"]
        parent["aliases"] = {"Actor": {name: "User" for name in parent["rules"]}}
    if boundary:
        docs[-1]["correlation"]["condition"]["percentile"] = 75
    if not any((grouped, mixed, metric, nested)):
        # These single-child Amount cases need neither User context nor a Host
        # partition. Keep relationship/group/metric coverage in the other cases.
        docs[0]["fields"] = ["Amount"]
        docs[1]["correlation"]["group-by"] = []
    (query,) = convert_documents(docs)
    metric_name = {"value_sum": "ValueSum", "value_avg": "ValueAvg", "value_percentile": "ValuePercentile"}[kind]
    groups = ["Actor"] if grouped else []
    if grouped:
        values = {"value_sum": (30 if nested else 45 if mixed else 40, 100),
                  "value_avg": (11.25 if mixed else 13.333333333333334, 100),
                  "value_percentile": (10, 100)}[kind]
        if metric:
            values = (5, 4)
        expected = f'"Alice",real({values[0]}), "Bob",real({values[1]})'
    else:
        value = 130 if nested else 5 if metric else {"value_sum": 145 if mixed else 140,
                                   "value_avg": 29 if mixed else 35,
                                   "value_percentile": 10 if mixed else 20}[kind]
        expected = f"real({value})"
    data = DATA
    if not any((grouped, mixed, metric, nested, boundary, empty)):
        # One child retaining 10,10,20 has observations 10,20.
        data = data.replace('];', '] | where Host == "h" and User == "Alice";')
        simple = {'value_sum': 30, 'value_avg': 15, 'value_percentile': 20}[kind]
        expected = f"real({simple})"
    if boundary or empty:
        vals = ('dynamic(9007199254740992),dynamic(9007199254740993),dynamic(9007199254740993),dynamic(0),dynamic(1e100),dynamic(1e100),dynamic(-1e100)' if boundary
                else 'dynamic("10"),dynamic(null),dynamic("bad")')
        data = ('let Events=datatable(Amount:dynamic) [' + vals + ']\n'
                '| extend Timestamp=datetime(2026-01-01T12:00:00Z),Host="h",Marker="match",User="Alice";\n')
        if empty:
            data = data.replace('| extend Timestamp=', '| union (print Amount=pack_array(real(nan))[0]), (print Amount=pack_array(real(+inf))[0]), (print Amount=pack_array(real(-inf))[0])\n| extend Timestamp=')
        # Adjacent longs must remain separate; doubles beyond decimal range must deduplicate.
        expected = '"9007199254740993"' if boundary else 'real(0)' if kind == 'value_sum' else ''
    projection = ', '.join([*(f'{g}=tostring({g})' for g in groups), f'Metric=toreal({metric_name})'])
    schema = ', '.join([*(f'{g}:string' for g in groups), 'Metric:string' if boundary else 'Metric:real'])
    if boundary:
        projection = f'Metric=tostring({metric_name})'
    keys = ', '.join([*groups, 'Metric'])
    return (data + 'let Actual=(){\n' + query + '\n};\n'
            + f'let Observed=Actual() | project {projection};\n'
            + f'let Expected=datatable({schema}) [{expected}];\n'
            + 'union (Observed | extend Delta=1), (Expected | extend Delta=-1)\n'
            + f'| summarize Delta=sum(Delta) by {keys}\n| where Delta!=0\n')


def multiple_children_fixture():
    docs = numeric_documents()
    source = source_document("second-source", "other")
    source["fields"] = ["User", "Amount"]
    child = correlation_document("second-child", "event_count", ["second-source"], condition={"gte": 1})
    docs[2:2] = [source, child]
    docs[-1]["correlation"]["rules"].append("second-child")
    (query,) = convert_documents(docs)
    data = DATA.replace('];', ', datetime(2026-01-01T12:00:00Z),"h3","other","Alice",dynamic(10) ];')
    return (data + 'let Actual=(){\n' + query + '\n};\n'
            + 'let Observed=Actual() | project Metric=toreal(ValueSum);\n'
            + 'let Expected=datatable(Metric:real) [150.0];\n'
            + 'union (Observed | extend Delta=1), (Expected | extend Delta=-1)\n'
            + '| summarize Delta=sum(Delta) by Metric\n| where Delta!=0\n')


def noqualifying_overflow_fixture():
    child = correlation_document("child", "value_sum", ["source-rule"], groups=(),
                                 condition={"field": "Amount", "eq": 0})
    parent = correlation_document("parent", "event_count", ["child"], groups=(), condition={"gte": 999})
    (query,) = convert_documents([source_document(), child, parent])
    return (
        '// Expected failure: Kusto value_sum integer overflow, despite no qualifying child or parent.\n'
        'let Events=datatable(Timestamp:datetime,Marker:string,Amount:long) ['
        'datetime(2026-01-01),"match",9223372036854775807,datetime(2026-01-01),"match",1];\n'
        + query + '\n'
    )


def fixtures():
    result = {}
    for kind in ('value_sum', 'value_avg', 'value_percentile'):
        result[f'numeric_context_{kind}.kql'] = fixture(kind)
        result[f'numeric_context_{kind}_groups.kql'] = fixture(kind, grouped=True, mixed=True)
        result[f'numeric_context_{kind}_empty.kql'] = fixture(kind, empty=True)
    result['numeric_context_metrics.kql'] = fixture('value_sum', metric=True, grouped=True)
    result['numeric_context_nested.kql'] = fixture('value_sum', nested=True)
    result['numeric_context_boundaries.kql'] = fixture('value_percentile', boundary=True)
    result['numeric_context_multiple_children.kql'] = multiple_children_fixture()
    result['numeric_context_noqual_overflow.kql'] = noqualifying_overflow_fixture()
    return result
