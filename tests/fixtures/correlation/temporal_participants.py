"""Freeze ordered branching workloads with an exhaustive, independent tuple oracle.

The old enumerating implementation and reachability implementation use the same
inputs and expected results. No engine calls occur during preparation.
"""

import itertools
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from tests.correlation_helpers import convert_documents, correlation_document, source_document
from tests.fixtures.correlation.benchmark import input_query, sha, timestamp


def freeze(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "workloads": []}
    for length, branches in ((3, 3), (5, 3), (7, 3), (5, 6)):
        markers = list("ABCDEFG"[:length])
        rows = [
            {
                "second": position * 10 + alternative,
                "Host": "h",
                "Marker": marker,
                "Amount": 1,
                "User": f"{marker}{alternative}",
            }
            for position, marker in enumerate(markers)
            for alternative in range(branches)
        ]
        expected = []
        paths = 0
        for end in sorted({row["second"] for row in rows}):
            candidates = [
                [row for row in rows if row["Marker"] == marker and end - 300 <= row["second"] <= end]
                for marker in markers
            ]
            participants = set()
            for sequence in itertools.product(*candidates):
                if sequence[-1]["second"] == end and all(
                    a["second"] < b["second"] for a, b in zip(sequence, sequence[1:])
                ):
                    paths += 1
                    participants.update(row["User"] for row in sequence)
            if participants:
                expected.append([timestamp(end - 300), timestamp(end), "h", length, sorted(participants)])
        for context in (False, True):
            name = f"ordered-length{length}-branch{branches}-{'context' if context else 'plain'}"
            documents = [source_document(marker, marker) for marker in markers]
            summary = correlation_document("OrderedSequence", "temporal_ordered", markers)
            if context:
                summary["fields"] = ["User"]
            (query,) = convert_documents([*documents, summary])
            fixture = {
                "schema": {"WindowStart": "datetime", "WindowEnd": "datetime", "Host": "string", "RuleCount": "long"},
                "expected": expected if context else [row[:-1] for row in expected],
                "rows": rows,
                "full_sequence_tuples": paths,
                "paired_source_rows": sum(
                    sum(row["second"] <= result_end for row in rows)
                    for result_end in [row["second"] for row in rows if row["Marker"] == markers[-1]]
                ),
            }
            if context:
                fixture["schema"]["User"] = "dynamic"
            fixture_path, query_path = destination / f"{name}.json", destination / f"{name}.kql"
            fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
            query_path.write_text(input_query(rows) + query + "\n")
            manifest["workloads"].append(
                {
                    "name": name,
                    "fixture": fixture_path.name,
                    "query": query_path.name,
                    "fixture_sha256": sha(fixture_path.read_bytes()),
                    "query_sha256": sha(query_path.read_bytes()),
                    "query_bytes": len(query.encode()),
                    "operators": dict(Counter(re.findall(r"\|\s*([a-z][\w-]*)", query))),
                }
            )
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def correctness_fixture(order):
    """Direct exhaustive product oracle; no reachability recurrence is reused."""
    from itertools import product

    rows = [
        (0, "a", "a0"),
        (1, "a", "a1"),
        (2, "b", "b2"),
        (2, "a", "tie"),
        (3, "c", "c3"),
        (4, "a", "a4"),
        (5, "b", "b5"),
        (6, "a", "a6"),
        (7, "c", "c7"),
        (8, "a", "a8"),
        (9, "b", "b9"),
        (10, "a", "a10"),
    ]
    markers = list(dict.fromkeys(order))
    documents = [source_document(marker, marker) for marker in markers]
    summary = correlation_document("RepeatedOrdered", "temporal_ordered", order, span="7m")
    summary["fields"] = ["User", "Server"]
    (query,) = convert_documents([*documents, summary])
    expected = {}
    for sequence in product(*[[row for row in rows if row[1] == marker] for marker in order]):
        if not all(left[0] < right[0] for left, right in zip(sequence, sequence[1:])):
            continue
        if sequence[-1][0] - sequence[0][0] > 7:
            continue
        expected.setdefault(sequence[-1][0], set()).update(row[2] for row in sequence)
    data = ",\n".join(
        f'datetime(2026-01-01T12:{minute:02}:00Z), "h", "{marker}", "{user}", "{user}-server"'
        for minute, marker, user in rows
    )
    expected_rows = ",\n".join(
        f"datetime(2026-01-01T12:{minute:02}:00Z), '{json.dumps(sorted(users), separators=(',', ':'))}', "
        f"'{json.dumps(sorted(user+'-server' for user in users), separators=(',', ':'))}', {len(markers)}, true"
        for minute, users in sorted(expected.items())
    )
    return f"""let Events=datatable(Timestamp:datetime, Host:string, Marker:string, User:string, Server:string) [
{data}
];
let Actual=() {{
{query}
}};
let Observed=Actual() | project WindowEnd, Users=tostring(array_sort_asc(User)), Servers=tostring(array_sort_asc(Server)), RuleCount, ContextComplete;
let Expected=datatable(WindowEnd:datetime, Users:string, Servers:string, RuleCount:long, ContextComplete:bool) [
{expected_rows}
];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by WindowEnd, Users, Servers, RuleCount, ContextComplete | where Delta != 0
"""


def fixtures():
    return {
        "participants_single.kql": correctness_fixture(["a"]),
        "participants_repeated_long.kql": correctness_fixture(["a", "b", "a", "c", "a"]),
        "participants_repeated_seven.kql": correctness_fixture(["a", "b", "a", "c", "a", "b", "a"]),
    }


def freeze_growth(source, destination, *, enumeration=False):
    """Freeze engine-count diagnostics for each context benchmark stage.

    Counts are literal consequences of the layered input population: each
    pre-final source has B alternatives, and there are B final completions.
    These are measured separately from timed full-result workloads.
    """
    from tests.fixtures.correlation.runtime import manifest

    destination.mkdir(parents=True, exist_ok=False)
    prepared = json.loads((source / "manifest.json").read_text())
    for item in prepared["workloads"]:
        if not item["name"].endswith("-context"):
            continue
        query = (source / item["query"]).read_text()
        fixture = json.loads((source / item["fixture"]).read_text())
        length, branches = map(int, re.search(r"length(\d+)-branch(\d+)", item["name"]).groups())
        final = "\n_sigma_context_summaries\n" if enumeration else "\nCorrelationMatches\n"
        definitions = query[: query.rindex(final)] + "\n"
        expected = {}
        if enumeration:
            expected["_sigma_context_matches"] = fixture["paired_source_rows"]
            for index in range(length):
                relation = "_sigma_context_path" + ("_" + str(index) if index else "")
                expected[relation] = branches ** (index + 1) * branches if index < length - 1 else branches**length
            expected["_sigma_context_selected"] = branches * ((length - 1) * branches + 1)
        else:
            expected["WindowSourceMatches"] = fixture["paired_source_rows"]
            for relation in re.findall(r"^let (Earliest\w+Prefix|Latest\w+Suffix) =", definitions, re.M):
                expected[relation] = branches
            expected["OrderedSourceRelationships"] = branches * ((length - 1) * branches + 1)
        observed = ", ".join(f'({name} | summarize Rows=count() | extend Stage="{name}")' for name in expected)
        expected_rows = ", ".join(f'"{name}", {count}' for name, count in expected.items())
        diagnostic = (
            definitions
            + f"""let Observed=union {observed};
let Expected=datatable(Stage:string, Rows:long) [{expected_rows}];
union (Observed | extend Delta=1), (Expected | extend Delta=-1)
| summarize Delta=sum(Delta) by Stage, Rows | where Delta != 0
"""
        )
        (destination / (item["name"] + ".kql")).write_text(diagnostic)
    (destination / "manifest.json").write_text(json.dumps(manifest(destination), indent=2) + "\n")
