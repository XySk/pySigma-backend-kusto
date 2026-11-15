"""Strict Kusto v2 multiset-diagnostic runner. No engine calls on import.

Run explicitly with --manifest, --output, and connection options after freezing
fixtures. A successful query must produce exactly one empty primary table and a
single successful completion. Expected overflow errors are named per fixture.
"""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

OVERFLOW_FIXTURES = {
    "value_sum_overflow.kql": "Kusto value_sum integer overflow",
    "value_sum_negative_overflow.kql": "Kusto value_sum integer overflow",
    "numeric_context_noqual_overflow.kql": "Kusto value_sum integer overflow",
}
DEFAULT_OPTIONS = {"max_memory_consumption_per_query_per_node": 12884901888}


def _error_frames(payload):
    if isinstance(payload, dict):
        return [payload["error"]] if payload.get("error") else []
    if not isinstance(payload, list):
        return []
    return [
        frame
        for frame in payload
        if isinstance(frame, dict)
        and (frame.get("FrameType") in ("Error", "QueryError") or frame.get("error") or frame.get("HasErrors") is True)
    ]


def read_primary_result(payload):
    """Require a complete, unambiguous successful v2 dataset, then return rows."""
    if not isinstance(payload, list) or not all(isinstance(frame, dict) for frame in payload):
        raise ValueError("response is not a v2 frame array")
    if _error_frames(payload):
        raise ValueError("engine error frame")
    completion = [frame for frame in payload if frame.get("FrameType") == "DataSetCompletion"]
    primary = [
        frame
        for frame in payload
        if frame.get("FrameType") == "DataTable" and frame.get("TableKind") == "PrimaryResult"
    ]
    if len(completion) != 1 or completion[0].get("HasErrors") is not False:
        raise ValueError("missing, duplicate, or unsuccessful completion frame")
    if len(primary) != 1 or not isinstance(primary[0].get("Rows"), list):
        raise ValueError("missing, duplicate, or malformed primary result")
    return primary[0]["Rows"]


def assess_response(payload, expected_error=None):
    if expected_error:
        errors = _error_frames(payload)
        if errors and expected_error in json.dumps(errors):
            return True, "expected engine error: " + expected_error
        return False, "expected engine error absent or wrong error category"
    try:
        rows = read_primary_result(payload)
    except ValueError as error:
        return False, str(error)
    return (False, f"multiset differences: {json.dumps(rows)}") if rows else (True, "empty multiset diagnostic")


def manifest(directory):
    return {
        "status": "PENDING",
        "scope": "available features only; ranges blocked by #31; partial ordered thresholds deferred",
        "fixtures": [
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "expected_error": OVERFLOW_FIXTURES.get(path.name),
            }
            for path in sorted(directory.glob("*.kql"))
        ],
    }


def request(endpoint, route, database, query, options, destination, timeout):
    request_path = destination.with_suffix(".request.json")
    response_path = destination.with_suffix(".response.json")
    body = {"db": database, "csl": query, "properties": json.dumps({"Options": options})}
    request_path.write_text(json.dumps(body))
    result = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--max-time",
            str(timeout),
            "--header",
            "Content-Type: application/json",
            "--header",
            "Accept: application/json",
            "--data-binary",
            "@" + str(request_path),
            "--output",
            str(response_path),
            "--write-out",
            "%{http_code}",
            endpoint.rstrip("/") + route,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    if result.returncode:
        raise ValueError(f"curl failed ({result.returncode}): {result.stderr.strip()}")
    try:
        payload = json.loads(response_path.read_text())
        status = int(result.stdout)
    except (ValueError, OSError) as error:
        raise ValueError("invalid HTTP/JSON response") from error
    return payload, status


def run(args):
    prepared = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    record = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.endpoint,
        "database": args.database,
        "request_options": DEFAULT_OPTIONS,
        "timeout_seconds": args.timeout,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "fixtures": [],
    }
    try:
        version, status = request(
            args.endpoint,
            "/v1/rest/mgmt",
            args.database,
            ".show version",
            {},
            args.output / "engine-version",
            args.timeout,
        )
        if status != 200 or not isinstance(version, dict) or not version.get("Tables"):
            raise ValueError("engine version request failed or lacked tables")
        record["engine_version"] = version
        for item in prepared["fixtures"]:
            if args.only and item["name"] not in args.only:
                continue
            started = time.monotonic()
            outcome = dict(item)
            try:
                path = args.manifest.parent / item["name"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("fixture hash differs from frozen manifest")
                payload, status = request(
                    args.endpoint,
                    "/v2/rest/query",
                    args.database,
                    path.read_text(),
                    DEFAULT_OPTIONS,
                    args.output / path.stem,
                    args.timeout,
                )
                passed, detail = assess_response(payload, item.get("expected_error"))
                if status != 200 and not item.get("expected_error"):
                    passed, detail = False, f"HTTP {status}: {detail}"
                if status >= 500:
                    passed, detail = False, f"HTTP {status}: {detail}"
                outcome.update(status="PASS" if passed else "FAIL", detail=detail, http_status=status)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                outcome.update(status="FAIL", detail=str(error))
            outcome["seconds"] = round(time.monotonic() - started, 3)
            record["fixtures"].append(outcome)
            print(item["name"], outcome["status"], outcome["detail"], flush=True)
        if not record["fixtures"]:
            raise ValueError("no fixtures selected")
        record["status"] = "PASS" if all(item["status"] == "PASS" for item in record["fixtures"]) else "FAIL"
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        record.update(status="FAIL", error=str(error))
    finally:
        (args.output / "results.json").write_text(json.dumps(record, indent=2) + "\n")
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("acceptance-manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://localhost:8080")
    parser.add_argument("--database", default="NetDefaultDB")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--only", nargs="+")
    raise SystemExit(run(parser.parse_args()))
