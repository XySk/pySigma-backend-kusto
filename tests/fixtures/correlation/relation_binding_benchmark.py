"""Focused public-conversion workloads for relation-binding policy decisions."""

import argparse
import json
from pathlib import Path

from tests.fixtures.correlation import benchmark
from tests.fixtures.correlation.focused_benchmark_manifest import FocusedBenchmarkManifest


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

    manifest = FocusedBenchmarkManifest.begin(
        destination,
        baseline_revision=benchmark.BASELINE_REVISION,
        oracle="direct inclusive-window enumeration with explicit Boolean participants",
    )
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
        manifest.add(
            name,
            fixture,
            benchmark.input_query(rows) + query + "\n",
            query,
            measured_query=query,
            input_rows=len(rows),
            expected_rows=len(expected),
        )
    manifest.write()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    freeze(parser.parse_args().output)
