import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
revision = sys.argv[2] if len(sys.argv) > 2 else None
if revision:
    import sigma.backends.kusto as package
    import sigma.backends.kusto.kusto as backend

    code = subprocess.check_output(["git", "show", revision + ":sigma/backends/kusto/kusto.py"], text=True)
    exec(compile(code, revision + ":sigma/backends/kusto/kusto.py", "exec"), backend.__dict__)
    package.KustoBackend = backend.KustoBackend
from tests.correlation_helpers import convert_documents, fixture_schema
from tests.fixtures.correlation import benchmark
from tests.fixtures.correlation.native_percentile import exact_prefilter, query as public_query

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=False)
record = {
    "revision": revision or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "baseline_revision": benchmark.BASELINE_REVISION,
    "oracle": "independent direct inclusive-window enumeration plus literal fixed group medians",
    "workloads": [],
}


def write(name, query, source, schema, expected, rowcount):
    fixture = {"name": name, "schema": schema, "expected": expected}
    fp = out / (name + ".json")
    fp.write_text(json.dumps(fixture, indent=2) + "\n")
    qp = out / (name + ".kql")
    qp.write_text(source + query + "\n")
    record["workloads"].append(
        {
            "name": name,
            "fixture": fp.name,
            "fixture_sha256": benchmark.sha(fp.read_bytes()),
            "query": qp.name,
            "query_sha256": benchmark.sha(qp.read_bytes()),
            "query_bytes": len(query.encode()),
            "query_lines": len(query.splitlines()),
            "operators": dict(Counter(re.findall(r"\|\s*([a-z][\w-]*)", query))),
            "input_rows": rowcount,
            "expected_rows": len(expected),
        }
    )


for kind in ("long", "real"):
    for profile in ("many-groups", "hot-group"):
        for context in (False, True):
            rows = benchmark.events(profile)
            if kind == "real":
                for row in rows:
                    row["Amount"] = row["Amount"] / 4.0
            docs = benchmark.documents("value_percentile", context, profile)
            # Retain a nonempty high-rate case for fractional real measurements.
            if kind == "real" and profile == "many-groups":
                docs[-1]["correlation"]["condition"]["gte"] = 0.5
            (query,) = convert_documents(docs, schema=fixture_schema(Amount=kind))
            name = f"{kind}-{profile}-value_percentile-" + ("context" if context else "plain")
            schema = {"WindowStart": "datetime", "WindowEnd": "datetime", "Host": "string", "ValuePercentile": kind}
            if context:
                schema["User"] = "dynamic"
            # benchmark oracle has a fixed threshold2; real rows/threshold both scaled4.
            oracle_rows = benchmark.events(profile)
            expected = benchmark.oracle(oracle_rows, "value_percentile", context, profile)
            if kind == "real":
                for row in expected:
                    row[3] = row[3] / 4.0
            source = benchmark.input_query(rows).replace("Amount:long", f"Amount:{kind}")
            write(name, query, source, schema, expected, len(rows))
if not revision:
    for threshold in (10, 90):
        for use_prefilter in (False, True):
            query = public_query(groups=("Host",), threshold=threshold)
            if use_prefilter:
                query = exact_prefilter(query, 50, "gte", threshold)
            source = """let Events=range GroupId from 0 to 99 step 1
| extend Slots=range(0,99) | mv-expand Slot=Slots to typeof(long)
| project Timestamp=datetime(2026-01-01T12:00:00Z)+Slot*1s,Host=tostring(GroupId),Amount=GroupId,Marker="match";
"""
            expected = [
                [benchmark.timestamp(s - 300), benchmark.timestamp(s), str(g), g]
                for g in range(threshold, 100)
                for s in range(100)
            ]
            write(
                f"strategy-{threshold}-" + ("prefilter" if use_prefilter else "direct"),
                query,
                source,
                {"WindowStart": "datetime", "WindowEnd": "datetime", "Host": "string", "ValuePercentile": "long"},
                expected,
                10000,
            )
(out / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
