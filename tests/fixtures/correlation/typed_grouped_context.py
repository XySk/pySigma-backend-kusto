"""Grouped retained-context acceptance with literal typed groups and relationships."""

from tests.correlation_helpers import convert_documents, correlation_document, source_document


def fixture(parent=False):
    source = source_document()
    source["fields"] = ["User", "Server"]
    child = correlation_document(
        "child", "event_count", ["source-rule"], groups=("Host", "Region"), condition={"gte": 1}
    )
    documents = [source, child]
    if parent:
        documents.append(
            correlation_document(
                "parent",
                "event_count",
                ["child"],
                groups=("Host", "Region", "User", "Server"),
                condition={"gte": 1},
            )
        )
    (query,) = convert_documents(documents)
    data = """let Events=datatable(Timestamp:datetime,Host:string,Region:string,Marker:string,User:string,Server:string) [
 datetime(2026-01-01T12:00:00Z),"one","east","match","Alice","server1",
 datetime(2026-01-01T12:00:00Z),"one","east","match","Bob","server2",
 datetime(2026-01-01T12:00:00Z),"two","east","match","Carol","server3",
 datetime(2026-01-01T12:00:00Z),"one","west","match","Dave","server4"
];
let Actual=(){
"""
    if parent:
        diagnostic = """let Observed=Actual() | project WindowEnd,Kind=gettype(Host),Host=tostring(Host),Region=tostring(Region),
 User=tostring(User),Server=tostring(Server),EventCount;
let Expected=datatable(WindowEnd:datetime,Kind:string,Host:string,Region:string,User:string,Server:string,EventCount:long) [
 datetime(2026-01-01T12:00:00Z),"string","one","east","Alice","server1",1,
 datetime(2026-01-01T12:00:00Z),"string","one","east","Bob","server2",1,
 datetime(2026-01-01T12:00:00Z),"string","two","east","Carol","server3",1,
 datetime(2026-01-01T12:00:00Z),"string","one","west","Dave","server4",1
];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,Host,Region,User,Server,EventCount
| where Delta!=0
"""
    else:
        diagnostic = """let Observed=Actual() | project WindowEnd,Kind=gettype(Host),Host=tostring(Host),Region=tostring(Region),EventCount,
 Users=tostring(array_sort_asc(User)),Servers=tostring(array_sort_asc(Server));
let Expected=datatable(WindowEnd:datetime,Kind:string,Host:string,Region:string,EventCount:long,Users:string,Servers:string) [
 datetime(2026-01-01T12:00:00Z),"string","one","east",2,'["Alice","Bob"]','["server1","server2"]',
 datetime(2026-01-01T12:00:00Z),"string","two","east",1,'["Carol"]','["server3"]',
 datetime(2026-01-01T12:00:00Z),"string","one","west",1,'["Dave"]','["server4"]'
];
union (Observed | extend Delta=1),(Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd,Kind,Host,Region,EventCount,Users,Servers
| where Delta!=0
"""
    return data + query + "\n};\n" + diagnostic


def fixtures():
    return {
        "typed_grouped_context.kql": fixture(),
        "typed_grouped_context_parent.kql": fixture(parent=True),
    }
