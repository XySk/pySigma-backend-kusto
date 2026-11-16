"""Consumer-aware count chains, independent window oracles, and engine inputs."""

import hashlib
import json
import random
from pathlib import Path

from sigma.pipelines.kusto_common.schema import FieldMappings
from tests.correlation_helpers import convert_documents, correlation_document, fixture_schema, source_document
from tests.fixtures.correlation.generate import HEADER, check


def documents(threshold, *, duplicate_searches=False, groups=("User",), child_span="5m", parent_span="3m"):
    failed = source_document("failed-logon", "failure")
    if duplicate_searches:
        failed["detection"]["alternate"] = {"Marker": "failure"}
        failed["detection"]["condition"] = ["selection", "alternate"]
    return [
        failed,
        source_document("successful-logon", "success"),
        correlation_document(
            "failed-logon-bursts", "event_count", ["failed-logon"], groups=groups,
            condition={"gte": threshold}, span=child_span,
        ),
        correlation_document(
            "failures-then-success", "temporal_ordered", ["failed-logon-bursts", "successful-logon"],
            groups=groups, span=parent_span,
        ),
    ]


def convert_pair(inputs, **kwargs):
    (specialized,) = convert_documents(inputs, **kwargs)
    # An observable callback requires the complete child metric, providing the
    # general path through the same public API without a test-only backend flag.
    (general,) = convert_documents(inputs, callback=lambda rule, output, index, condition, query: query, **kwargs)
    return specialized, general


def timestamp(ticks):
    seconds, fraction = divmod(ticks, 10_000_000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"datetime(2026-01-01T{hours:02}:{minutes:02}:{seconds:02}.{fraction:07})"


def expected_chain(events, threshold, *, source_multiplicity=1, child_seconds=300, parent_seconds=180):
    """Brute force the contract, independently of predecessor and scan plans."""
    groups = {user for _, user, _ in events if user}
    expected = []
    for user in sorted(groups):
        failures = [time for time, group, marker in events if group == user and marker == "failure"]
        bursts = {
            anchor for anchor in failures
            if source_multiplicity * sum(anchor - child_seconds * 10_000_000 <= time <= anchor for time in failures)
            >= threshold
        }
        successes = {time for time, group, marker in events if group == user and marker == "success"}
        expected.extend(
            (time, user) for time in sorted(successes)
            if any(time - parent_seconds * 10_000_000 <= burst < time for burst in bursts)
        )
    return expected


def randomized_fixture(threshold, duplicate_searches=False):
    rng = random.Random(4800 + threshold)
    events = []
    for group in range(18):
        user = f"account-{group:02}"
        for _ in range(75):
            # Minute-scale timestamp ties plus one-tick boundary offsets.
            ticks = rng.randrange(35) * 60 * 10_000_000 + rng.choice([0, 0, 0, 1])
            marker = rng.choice(["failure", "failure", "success"])
            events.append((ticks, user, marker))
            if rng.randrange(5) == 0:
                events.append((ticks, user, marker))
    # Group transitions and missing/empty target accounts cannot borrow matches.
    events.extend([(0, "", "failure"), (1, "", "success")])
    specialized, general = convert_pair(documents(threshold, duplicate_searches=duplicate_searches))
    data = "let Events = datatable(Timestamp:datetime, User:string, Marker:string) [\n" + ",\n".join(
        f'{timestamp(time)}, {json.dumps(user)}, {json.dumps(marker)}' for time, user, marker in events
    ) + "\n];\n"
    expected_rows = expected_chain(events, threshold, source_multiplicity=2 if duplicate_searches else 1)
    expected = "let Expected = datatable(WindowEnd:datetime, User:string) [\n" + ",\n".join(
        f'{timestamp(time)}, {json.dumps(user)}' for time, user in expected_rows
    ) + "\n] | extend WindowStart = WindowEnd - 3m, RuleCount = tolong(2);\n"
    fields = ["WindowStart", "WindowEnd", "User", "RuleCount"]
    return (
        HEADER + data + f"let Specialized = () {{\n{specialized}\n}};\nlet General = () {{\n{general}\n}};\n"
        + expected
        + "let Actual = union (Specialized() | extend Plan=\"specialized\"), (General() | extend Plan=\"general\");\n"
        + "let BothExpected = union (Expected | extend Plan=\"specialized\"), (Expected | extend Plan=\"general\");\n"
        + check("Actual", "BothExpected", ["Plan", *fields])
    )


def targeted_queries():
    inputs = documents(10, child_span="10m", parent_span="10m")
    for source, event_id in zip(inputs, (4625, 4624)):
        source["detection"]["selection"] = {"EventID": event_id}
    return convert_pair(
        inputs, tables=("SecurityEvent", "NetworkEvents"),
        schema=fixture_schema(("SecurityEvent", "NetworkEvents"), TimeGenerated="datetime", TargetUserName="string", EventID="long"),
        mappings=FieldMappings(generic_mappings={"User": "TargetUserName", "Timestamp": "TimeGenerated"}),
    )


def targeted_fixture():
    specialized, general = targeted_queries()
    # Each account describes an independent literal worked case. Computers and
    # SubjectUserName intentionally differ; the target account is the identity.
    data = '''let SecurityEvent = union
(range Minute from 0 to 9 step 1 | project TimeGenerated=datetime(2026-01-01)+Minute*1m,
 EventID=tolong(4625),TargetUserName="distributed",Computer=strcat("host-",Minute),SubjectUserName="system"),
(datatable(TimeGenerated:datetime,EventID:long,TargetUserName:string,Computer:string,SubjectUserName:string) [
 datetime(2026-01-01T00:18:00),4624,"distributed","success-host","other-subject",
 datetime(2026-01-01T00:19:00),4624,"distributed","another-host","other-subject",
 datetime(2026-01-01T00:19:00.0000001),4624,"distributed","late-host","other-subject"
]),
(range Match from 1 to 10 step 1 | project TimeGenerated=datetime(2026-01-01T00:00:00),
 EventID=tolong(4625),TargetUserName="tied-only",Computer="failure-host",SubjectUserName="system"),
(datatable(TimeGenerated:datetime,EventID:long,TargetUserName:string,Computer:string,SubjectUserName:string) [
 datetime(2026-01-01T00:00:00),4624,"tied-only","success-host","system",
 datetime(2026-01-01T00:00:00.0000001),4624,"tied-only","success-host","system"
]),
(range Match from 1 to 10 step 1 | project TimeGenerated=datetime(2026-01-01T00:00:00),
 EventID=tolong(4625),TargetUserName="older-burst",Computer="first-host",SubjectUserName="system"),
(range Match from 1 to 10 step 1 | project TimeGenerated=datetime(2026-01-01T00:05:00),
 EventID=tolong(4625),TargetUserName="older-burst",Computer="second-host",SubjectUserName="system"),
(datatable(TimeGenerated:datetime,EventID:long,TargetUserName:string,Computer:string,SubjectUserName:string) [
 datetime(2026-01-01T00:05:00),4624,"older-burst","success-host","system"
]);
'''
    expected = '''let Expected = datatable(WindowEnd:datetime,User:string) [
 datetime(2026-01-01T00:18:00),"distributed",
 datetime(2026-01-01T00:19:00),"distributed",
 datetime(2026-01-01T00:00:00.0000001),"tied-only",
 datetime(2026-01-01T00:05:00),"older-burst"
] | extend WindowStart=WindowEnd-10m,RuleCount=tolong(2);
'''
    return (
        HEADER + data + f"let Specialized = () {{\n{specialized}\n}};\nlet General = () {{\n{general}\n}};\n" + expected
        + 'let Actual=union (Specialized() | extend Plan="specialized"),(General() | extend Plan="general");\n'
        + 'let BothExpected=union (Expected | extend Plan="specialized"),(Expected | extend Plan="general");\n'
        + check("Actual", "BothExpected", ["Plan", "WindowStart", "WindowEnd", "User", "RuleCount"])
    )


def fixtures():
    return {
        "count_existence_targeted_logon.kql": targeted_fixture(),
        "count_existence_one.kql": randomized_fixture(1),
        "count_existence_duplicates.kql": randomized_fixture(3, duplicate_searches=True),
        "count_existence_ten.kql": randomized_fixture(10),
    }


if __name__ == "__main__":
    directory = Path(__file__).parent
    prepared = []
    for name, query in fixtures().items():
        path = directory / name
        path.write_text(query)
        prepared.append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "expected_error": None})
    (directory / "count-existence-manifest.json").write_text(json.dumps({"status": "PENDING", "fixtures": prepared}, indent=2) + "\n")
