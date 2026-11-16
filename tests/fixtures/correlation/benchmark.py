"""Freeze public-conversion scalar workloads and measure unchanged artifacts.

The oracle deliberately enumerates actual windows, independently of generated
KQL. No private backend builder is imported. Importing this module makes no
engine calls. Use ``python -m tests.fixtures.correlation.benchmark --help``.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from sigma.backends.kusto import KustoBackend
from sigma.collection import SigmaCollection
from sigma.pipelines.kusto_common.schema import BaseSchema, FieldInfo, TableSchema
from sigma.processing.pipeline import ProcessingPipeline
from tests.fixtures.correlation.runtime import read_primary_result

BASELINE_REVISION = "07525ee846878754bda7933eab0684895a156086"
TYPES = ("event_count", "value_count", "value_sum", "value_avg", "value_percentile", "temporal", "temporal_ordered")
METRICS = dict(
    zip(TYPES, ("EventCount", "ValueCount", "ValueSum", "ValueAvg", "ValuePercentile", "RuleCount", "RuleCount"))
)
PROFILES = ("sparse", "dense", "many-groups", "hot-group")
EPOCH = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
MEMORY_LIMIT = 12884901888


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(seconds):
    return (EPOCH + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def events(profile):
    """Fixed scalar populations: tie/boundary cases and several distributions."""
    if profile == "sparse":
        return [
            {"second": second, "Host": "g0", "Marker": marker, "Amount": amount, "User": user}
            for second, marker, amount, user in [
                (0, "A", 2, "u0"),
                (0, "A", 2, "u0"),
                (300, "B", 4, "u1"),
                (601, "C", 6, "u2"),
            ]
        ]
    groups, size = (12, 8) if profile == "many-groups" else (1, 96 if profile == "hot-group" else 24)
    return [
        {
            "second": index // 2 if profile == "hot-group" else index * 10,
            "Host": f"g{group}",
            "Marker": "ABCD"[index % (4 if profile == "hot-group" else 3)],
            "Amount": 2 + index % 3,
            "User": f"u{index % 2}" if profile == "hot-group" else f"u{group}-{index}",
        }
        for group in range(groups)
        for index in range(size)
    ]


def documents(kind, context, profile, chain=False):
    markers = list("ABCD" if profile == "hot-group" else "ABC") if kind.startswith("temporal") else [None]
    docs = []
    refs = []
    for marker in markers:
        name = f"Source{marker or 'Events'}"
        refs.append(name)
        source = {
            "title": name,
            "name": name,
            "logsource": {"product": "benchmark"},
            "detection": {"selection": {"Marker": marker or list("ABCD")}, "condition": "selection"},
        }
        docs.append(source)
    threshold = 10000 if profile == "hot-group" and not kind.startswith("temporal") else 2
    condition = {"gte": threshold}
    if kind.startswith("value_"):
        condition["field"] = "User" if kind == "value_count" else "Amount"
    if kind == "value_percentile":
        condition["percentile"] = 50
    if kind.startswith("temporal"):
        condition = {"gte": len(markers)}
    corr = {
        "title": "ScalarCorrelation",
        "name": "ScalarCorrelation",
        "correlation": {"type": kind, "rules": refs, "group-by": ["Host"], "timespan": "5m", "condition": condition},
    }
    if context:
        corr["fields"] = ["User"]
    docs.append(corr)
    if chain:
        # A child contributes one match at WindowEnd; direct D source contributes
        # separately. A second parent makes accumulated dependency paths explicit.
        direct = {
            "title": "DirectD",
            "name": "DirectD",
            "logsource": {"product": "benchmark"},
            "detection": {"selection": {"Marker": "D"}, "condition": "selection"},
        }
        docs.append(direct)
        for name, sources in [("MixedParent", ["ScalarCorrelation", "DirectD"]), ("MultilevelParent", ["MixedParent"])]:
            parent = {
                "title": name,
                "name": name,
                "correlation": {
                    "type": "event_count",
                    "rules": sources,
                    "group-by": ["Host"],
                    "timespan": "5m",
                    "condition": {"gte": 1},
                },
            }
            if context:
                parent["fields"] = ["User"]
            docs.append(parent)
    return docs


def oracle(rows, kind, context, profile, chain=False):
    """Exhaustive scalar window oracle, independent of backend planning."""
    threshold = 10000 if profile == "hot-group" and not kind.startswith("temporal") else 2
    markers = "ABCD" if profile == "hot-group" else "ABC"
    results = []
    for group in sorted({row["Host"] for row in rows}):
        group_rows = [row for row in rows if row["Host"] == group]
        for end in sorted({row["second"] for row in group_rows}):
            window = [row for row in group_rows if end - 300 <= row["second"] <= end]
            participants = window
            values = sorted(row["Amount"] for row in window)
            if kind == "temporal_ordered":

                @lru_cache(None)
                def prefix(position, before):
                    found = set()
                    for index, row in enumerate(window):
                        if row["Marker"] != markers[position] or row["second"] >= before:
                            continue
                        previous = prefix(position - 1, row["second"]) if position else frozenset({index})
                        if previous:
                            found.update(previous)
                            found.add(index)
                    return frozenset(found)

                indexes = set()
                for index, row in enumerate(window):
                    if row["second"] == end and row["Marker"] == markers[-1]:
                        previous = prefix(len(markers) - 2, end)
                        if previous:
                            indexes.update(previous)
                            indexes.add(index)
                if not indexes:
                    continue
                participants = [window[index] for index in indexes]
                metric, qualifies = len(markers), True
            elif kind == "temporal":
                metric = len({row["Marker"] for row in window})
                qualifies = metric == len(markers)
            else:
                metric = {
                    "event_count": lambda: len(window),
                    "value_count": lambda: len({row["User"] for row in window}),
                    "value_sum": lambda: sum(values),
                    "value_avg": lambda: sum(values) / len(values),
                    "value_percentile": lambda: values[max(0, (len(values) + 1) // 2 - 1)],
                }[kind]()
                qualifies = metric >= threshold
            if qualifies:
                result = [timestamp(end - 300), timestamp(end), group, metric]
                if context:
                    result.append(sorted({row["User"] for row in participants}))
                results.append(result)
    if chain:
        contributions = [
            {
                "second": int((datetime.fromisoformat(row[1].replace("Z", "+00:00")) - EPOCH).total_seconds()),
                "Host": row[2],
                "users": row[4] if context else [],
            }
            for row in results
        ]
        contributions.extend(
            {"second": row["second"], "Host": row["Host"], "users": [row["User"]]}
            for row in rows
            if row["Marker"] == "D"
        )
        for _ in range(2):
            parents = []
            for group in sorted({row["Host"] for row in contributions}):
                grouped = [row for row in contributions if row["Host"] == group]
                for end in sorted({row["second"] for row in grouped}):
                    window = [row for row in grouped if end - 300 <= row["second"] <= end]
                    users = sorted({user for row in window for user in row["users"]})
                    parents.append({"second": end, "Host": group, "users": users, "metric": len(window)})
            contributions = parents
        results = [
            [timestamp(row["second"] - 300), timestamp(row["second"]), row["Host"], row["metric"]]
            + ([row["users"]] if context else [])
            for row in contributions
        ]
    return results


def convert(docs):
    pipeline = ProcessingPipeline.from_dict(
        {"transformations": [{"type": "set_state", "key": "query_table", "val": "Events"}]}
    )
    # Existing public partial-schema mechanism, also valid before the redesign.
    pipeline.vars["kusto_schema"] = BaseSchema(
        tables={
            "Events": TableSchema(
                fields={
                    name: FieldInfo(typ, "benchmark scalar input")
                    for name, typ in {
                        "Timestamp": "datetime",
                        "Host": "string",
                        "Marker": "string",
                        "Amount": "long",
                        "User": "string",
                    }.items()
                }
            )
        }
    )
    (query,) = KustoBackend(processing_pipeline=pipeline, correlation_timestamp_field="Timestamp").convert(
        SigmaCollection.from_yaml(yaml.safe_dump_all(docs))
    )
    return query


def input_query(rows):
    records = [
        f'datetime({timestamp(row["second"])}), {json.dumps(row["Host"])}, '
        f'{json.dumps(row["Marker"])}, {row["Amount"]}, {json.dumps(row["User"])}'
        for row in rows
    ]
    return (
        "let Events = datatable(Timestamp:datetime, Host:string, Marker:string, Amount:long, User:string) [\n"
        + ",\n".join(records)
        + "\n];\n"
    )


def freeze(destination, profiles=PROFILES):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise FileExistsError("frozen manifest already exists; choose a new output directory")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    record = {
        "format": 1,
        "revision": revision,
        "baseline_revision": BASELINE_REVISION,
        "oracle": "direct inclusive-window enumeration; ordered strict-time prefix witnesses",
        "workloads": [],
    }
    for profile in profiles:
        rows = events(profile)
        cases = [(kind, context, False) for kind in TYPES for context in (False, True)]
        cases.extend(("event_count", context, True) for context in (False, True))
        for kind, context, chain in cases:
            name = f"{profile}-{'mixed-multilevel' if chain else kind}-{'context' if context else 'plain'}"
            docs = documents(kind, context, profile, chain)
            query = convert(docs)
            metric = "EventCount" if chain else METRICS[kind]
            schema = {
                "WindowStart": "datetime",
                "WindowEnd": "datetime",
                "Host": "string",
                metric: "real" if kind == "value_avg" and not chain else "long",
            }
            if context:
                schema["User"] = "dynamic"
            expected = oracle(rows, kind, context, profile, chain)
            fixture = {
                "name": name,
                "profile": profile,
                "kind": kind,
                "context": context,
                "chain": chain,
                "documents": docs,
                "rows": rows,
                "schema": schema,
                "expected": expected,
                "contract_changes": [
                    "Host output was dynamic before scalar redesign; values comparable via explicit string projection",
                    "ValuePercentile output was dynamic before scalar redesign; native scalar type required after redesign",
                    "ContextComplete is new output; require true for these below-limit scalar populations",
                ],
            }
            fixture_path = destination / (name + ".json")
            fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
            query_path = destination / (name + ".kql")
            query_path.write_text(input_query(rows) + query + "\n")
            operators = Counter(re.findall(r"\|\s*([a-z][\w-]*)", query))
            record["workloads"].append(
                {
                    "name": name,
                    "fixture": fixture_path.name,
                    "fixture_sha256": sha(fixture_path.read_bytes()),
                    "query": query_path.name,
                    "query_sha256": sha(query_path.read_bytes()),
                    "query_bytes": len(query.encode()),
                    "query_lines": len(query.splitlines()),
                    "operators": dict(sorted(operators.items())),
                    "input_rows": len(rows),
                    "expected_rows": len(expected),
                }
            )
    (destination / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")


def assess(payload, fixture, baseline=False):
    try:
        rows = read_primary_result(payload)
    except ValueError as error:
        return False, str(error)
    primary = next(frame for frame in payload if frame.get("TableKind") == "PrimaryResult")
    columns = primary.get("Columns", [])
    schema = {column.get("ColumnName"): column.get("ColumnType") for column in columns}
    if fixture.get("context") and not baseline and schema.get("ContextComplete") != "bool":
        return False, "candidate context output requires ContextComplete:bool"
    # Pre-redesign grouping was dynamic. Record this intentional migration
    # separately; compare values after checking the explicitly allowed schema.
    for column, typ in fixture["schema"].items():
        accepted = {typ}
        if baseline and column in ("Host", "ValueSum", "ValuePercentile"):
            accepted.add("dynamic")
        if schema.get(column) not in accepted:
            return False, f"schema mismatch {column}: expected {sorted(accepted)}, observed {schema.get(column)}"
    indexes = [
        next(index for index, col in enumerate(columns) if col["ColumnName"] == name) for name in fixture["schema"]
    ]
    observed = []
    for row in rows:
        projected = [row[index] for index in indexes]
        for index, name in enumerate(fixture["schema"]):
            if name.startswith("Window"):
                projected[index] = (
                    datetime.fromisoformat(projected[index].replace("Z", "+00:00")).isoformat().replace("+00:00", "Z")
                )
            elif name == "User":
                value = json.loads(projected[index]) if isinstance(projected[index], str) else projected[index]
                projected[index] = sorted(value)
        observed.append(projected)
    if "ContextComplete" in schema and any(row[list(schema).index("ContextComplete")] is not True for row in rows):
        return False, "unexpected incomplete context on below-limit population"

    def key(row):
        row = list(row)
        row[3] = float(row[3])
        return json.dumps(row, sort_keys=True)

    actual = Counter(key(row) for row in observed)
    expected = Counter(key(row) for row in fixture["expected"])
    if actual != expected:
        return (
            False,
            f"multiset mismatch: missing={list((expected - actual).items())[:3]}, extra={list((actual - expected).items())[:3]}",
        )
    return True, "exact result multiset and declared schema"


def request(args, route, query, destination, options=None):
    request_path = destination.with_suffix(".request.json")
    response_path = destination.with_suffix(".response.json")
    request_path.write_text(
        json.dumps({"db": args.database, "csl": query, "properties": json.dumps({"Options": options or {}})})
    )
    outcome = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--max-time",
            str(args.timeout),
            "--header",
            "Content-Type: application/json",
            "--data-binary",
            "@" + str(request_path),
            "--output",
            str(response_path),
            "--write-out",
            "%{http_code}",
            args.endpoint.rstrip("/") + route,
        ],
        capture_output=True,
        text=True,
        timeout=args.timeout + 5,
    )
    destination.with_suffix(".transport.json").write_text(
        json.dumps({"returncode": outcome.returncode, "stdout": outcome.stdout, "stderr": outcome.stderr})
    )
    if outcome.returncode:
        raise ValueError(f"curl failed ({outcome.returncode}): {outcome.stderr.strip()}")
    payload = json.loads(response_path.read_text())
    if int(outcome.stdout) != 200:
        raise ValueError(f"HTTP {outcome.stdout}: {str(payload)[:500]}")
    return payload


def durable(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(path)


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    prepared = json.loads(args.manifest.read_text())
    record = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": sha(args.manifest.read_bytes()),
        "revision": prepared["revision"],
        "endpoint": args.endpoint,
        "database": args.database,
        "request_options": {
            "max_memory_consumption_per_query_per_node": args.memory_limit,
            "query_results_cache_max_age": "00:00:00",
            "servertimeout": f"00:00:{max(1, args.timeout - 2):02d}",
        },
        "timeout_seconds": args.timeout,
        "repeats": args.repeats,
        "status": "RUNNING",
        "attempts": [],
        "unobservable": [
            "intermediate row growth (no public per-operator profile captured)",
            "CPU/peak memory when absent from engine QueryCompletionInformation",
        ],
    }
    try:
        if args.container:
            inspection = subprocess.run(
                [args.docker, "inspect", args.container], capture_output=True, text=True, check=True
            )
            (args.output / "container-inspect.json").write_text(inspection.stdout)
            container = json.loads(inspection.stdout)[0]
            record["container"] = {
                "image": container["Image"],
                "memory": container["HostConfig"]["Memory"],
                "memory_swap": container["HostConfig"]["MemorySwap"],
                "cpu_quota": container["HostConfig"].get("CpuQuota"),
                "nano_cpus": container["HostConfig"].get("NanoCpus"),
            }
            if record["container"]["memory"] != args.memory_limit:
                raise ValueError("container memory differs from requested fixed baseline limit")
        version = request(args, "/v1/rest/mgmt", ".show version", args.output / "engine-version")
        if not isinstance(version, dict) or not version.get("Tables"):
            raise ValueError("engine version response lacks Tables")
        record["engine_version"] = {"status": "PASS", "response": version}
        if args.expected_engine and args.expected_engine not in json.dumps(version):
            raise ValueError("engine version differs from required baseline")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        record.update(status="FAIL", error=str(error))
        record.setdefault("engine_version", {"status": "FAIL", "detail": str(error)})
        durable(args.output / "results.json", record)
        return 1
    durable(args.output / "results.json", record)
    for item in prepared["workloads"]:
        if args.only and item["name"] not in args.only:
            continue
        fixture_path, query_path = (args.manifest.parent / item[key] for key in ("fixture", "query"))
        if (
            sha(fixture_path.read_bytes()) != item["fixture_sha256"]
            or sha(query_path.read_bytes()) != item["query_sha256"]
        ):
            record.update(status="FAIL", error="frozen artifact hash mismatch: " + item["name"])
            durable(args.output / "results.json", record)
            return 1
        fixture = json.loads(fixture_path.read_text())
        for repetition in range(args.repeats):
            attempt = {
                "name": item["name"],
                "repeat": repetition + 1,
                "query_sha256": item["query_sha256"],
                "fixture_sha256": item["fixture_sha256"],
                "cpu": None,
                "peak_memory": None,
                "intermediate_growth": None,
            }
            destination = args.output / f'{item["name"]}-{repetition + 1}'
            started = time.monotonic()
            try:
                payload = request(
                    args, "/v2/rest/query", query_path.read_text(), destination, record["request_options"]
                )
                passed, detail = assess(payload, fixture, baseline=prepared["revision"] == BASELINE_REVISION)
                attempt.update(status="PASS" if passed else "FAIL", detail=detail)
                for frame in payload if isinstance(payload, list) else []:
                    if frame.get("TableKind") == "PrimaryResult":
                        attempt["observed_schema"] = frame.get("Columns")
                        attempt["observed_rows"] = len(frame.get("Rows", []))
                    if frame.get("TableKind") == "QueryCompletionInformation":
                        attempt["engine_statistics"] = frame
                        columns = [column["ColumnName"] for column in frame.get("Columns", [])]
                        for values in frame.get("Rows", []):
                            row = dict(zip(columns, values))
                            if row.get("EventTypeName") == "QueryResourceConsumption":
                                stats = json.loads(row["Payload"])
                                usage = stats.get("resource_usage", {})
                                attempt["cpu"] = usage.get("cpu", {}).get("total cpu")
                                attempt["peak_memory"] = usage.get("memory", {}).get("peak_per_node")
                                attempt["engine_elapsed_seconds"] = stats.get("ExecutionTime")
                                attempt["output_statistics"] = stats.get("dataset_statistics")
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                attempt.update(status="FAIL", detail=str(error))
            except KeyboardInterrupt:
                attempt.update(status="FAIL", detail="client interrupted; query completion unverified")
                raise
            finally:
                attempt["elapsed_seconds"] = round(time.monotonic() - started, 6)
                record["attempts"].append(attempt)
                record["status"] = "FAIL" if any(row["status"] == "FAIL" for row in record["attempts"]) else "RUNNING"
                durable(args.output / "results.json", record)
            print(item["name"], repetition + 1, attempt["status"], attempt["detail"], flush=True)
    record["status"] = (
        "PASS" if record["attempts"] and all(row["status"] == "PASS" for row in record["attempts"]) else "FAIL"
    )
    durable(args.output / "results.json", record)
    return 0 if record["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    frozen = commands.add_parser("freeze")
    frozen.add_argument("--output", type=Path, required=True)
    measured = commands.add_parser("run")
    measured.add_argument("--manifest", type=Path, required=True)
    measured.add_argument("--output", type=Path, required=True)
    measured.add_argument("--endpoint", default="http://localhost:8080")
    measured.add_argument("--database", default="NetDefaultDB")
    measured.add_argument("--timeout", type=int, default=15)
    measured.add_argument("--repeats", type=int, default=3)
    measured.add_argument("--only", nargs="+")
    measured.add_argument("--container")
    measured.add_argument("--docker", default="docker")
    measured.add_argument("--expected-engine")
    measured.add_argument("--memory-limit", type=int, default=MEMORY_LIMIT)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.output)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
