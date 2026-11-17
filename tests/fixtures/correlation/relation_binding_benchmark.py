"""Focused public-conversion workloads for relation-binding policy decisions."""

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from tests.fixtures.correlation import benchmark


def _multiple_display_expected(rows):
    expected = []
    for row in benchmark.oracle(rows, "event_count", True, "dense"):
        end = next(
            item["second"] for item in rows if benchmark.timestamp(item["second"]) == row[1] and item["Host"] == row[2]
        )
        window = [item for item in rows if item["Host"] == row[2] and end - 300 <= item["second"] <= end]
        expected.append([*row, sorted({item["Amount"] for item in window})])
    return expected


def _boolean_expected(rows):
    expected = []
    for group in sorted({row["Host"] for row in rows}):
        grouped = [row for row in rows if row["Host"] == group]
        for end in sorted({row["second"] for row in grouped}):
            window = [row for row in grouped if end - 300 <= row["second"] <= end]
            markers = {row["Marker"] for row in window}
            ab = {"A", "B"}.issubset(markers)
            c = "C" in markers
            if not (ab or c):
                continue
            participating = [row for row in window if (ab and row["Marker"] in "AB") or (c and row["Marker"] == "C")]
            expected.append(
                [
                    benchmark.timestamp(end - 300),
                    benchmark.timestamp(end),
                    group,
                    len(markers),
                    sorted({row["User"] for row in participating}),
                ]
            )
    return expected


def _boolean_documents():
    sources = [
        {
            "title": marker,
            "name": marker,
            "logsource": {"product": "benchmark"},
            "detection": {"selection": {"Marker": marker}, "condition": "selection"},
        }
        for marker in "ABC"
    ]
    return [
        *sources,
        {
            "title": "BooleanContext",
            "name": "BooleanContext",
            "correlation": {
                "type": "temporal",
                "condition": "(A and B) or C",
                "group-by": ["Host"],
                "timespan": "5m",
            },
            "fields": ["User"],
        },
    ]


def freeze(destination: Path):
    """Freeze focused cases without changing the established 64-workload corpus."""
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise FileExistsError("frozen manifest already exists; choose a new output directory")
    cases = []
    for name, profile in (
        ("sparse-single-display", "sparse"),
        ("dense-unique-single-display", "dense"),
        ("hot-repeated-single-display", "hot-group"),
    ):
        rows = benchmark.events(profile)
        cases.append(
            (
                name,
                rows,
                benchmark.documents("event_count", True, profile),
                benchmark.oracle(rows, "event_count", True, profile),
                "EventCount",
                ["User"],
            )
        )

    rows = benchmark.events("dense")
    multiple = benchmark.documents("event_count", True, "dense")
    multiple[-1]["fields"] = ["User", "Amount"]
    cases.append(
        ("dense-multiple-display", rows, multiple, _multiple_display_expected(rows), "EventCount", ["User", "Amount"])
    )
    cases.append(("dense-boolean-context", rows, _boolean_documents(), _boolean_expected(rows), "RuleCount", ["User"]))

    rows = benchmark.events("hot-group")
    cases.append(
        (
            "hot-ordered-context",
            rows,
            benchmark.documents("temporal_ordered", True, "hot-group"),
            benchmark.oracle(rows, "temporal_ordered", True, "hot-group"),
            "RuleCount",
            ["User"],
        )
    )
    rows = benchmark.events("dense")
    cases.append(
        (
            "dense-child-reuse",
            rows,
            benchmark.documents("event_count", True, "dense", chain=True),
            benchmark.oracle(rows, "event_count", True, "dense", chain=True),
            "EventCount",
            ["User"],
        )
    )

    record = {
        "format": 1,
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "baseline_revision": benchmark.BASELINE_REVISION,
        "oracle": "direct inclusive-window enumeration with explicit Boolean participants",
        "workloads": [],
    }
    for name, rows, documents, expected, metric, context_fields in cases:
        query = benchmark.convert(documents)
        schema = {
            "WindowStart": "datetime",
            "WindowEnd": "datetime",
            "Host": "string",
            metric: "long",
            **{field: "dynamic" for field in context_fields},
        }
        fixture = {
            "name": name,
            "context": True,
            "context_fields": context_fields,
            "documents": documents,
            "rows": rows,
            "schema": schema,
            "expected": expected,
        }
        fixture_path = destination / f"{name}.json"
        query_path = destination / f"{name}.kql"
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
        query_path.write_text(benchmark.input_query(rows) + query + "\n")
        operators = Counter(re.findall(r"\|\s*([a-z][\w-]*)", query))
        record["workloads"].append(
            {
                "name": name,
                "fixture": fixture_path.name,
                "fixture_sha256": benchmark.sha(fixture_path.read_bytes()),
                "query": query_path.name,
                "query_sha256": benchmark.sha(query_path.read_bytes()),
                "query_bytes": len(query.encode()),
                "query_lines": len(query.splitlines()),
                "operators": dict(sorted(operators.items())),
                "input_rows": len(rows),
                "expected_rows": len(expected),
            }
        )
    (destination / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    freeze(parser.parse_args().output)
