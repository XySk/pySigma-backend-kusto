"""Shared manifest writer for the two focused correlation benchmark suites."""

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.correlation import benchmark


@dataclass
class FocusedBenchmarkManifest:
    """Freeze focused fixtures and their common query metadata."""

    destination: Path
    record: dict

    @classmethod
    def begin(cls, destination: Path, **metadata):
        destination.mkdir(parents=True, exist_ok=True)
        if (destination / "manifest.json").exists():
            raise FileExistsError("frozen manifest already exists; choose a new output directory")
        return cls(
            destination,
            {
                "format": 1,
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                **metadata,
                "workloads": [],
            },
        )

    def add(self, name, fixture, frozen_query, operator_query, *, measured_query=None, **metadata):
        fixture_path = self.destination / f"{name}.json"
        query_path = self.destination / f"{name}.kql"
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
        query_path.write_text(frozen_query)
        fixture_hash = benchmark.sha(fixture_path.read_bytes())
        measured_query = frozen_query if measured_query is None else measured_query
        operators = Counter(re.findall(r"\|\s*([a-z][\w-]*)", operator_query))
        self.record["workloads"].append(
            {
                "name": name,
                "fixture": fixture_path.name,
                "fixture_sha256": fixture_hash,
                "workload_sha256": fixture_hash,
                "query": query_path.name,
                "query_sha256": benchmark.sha(query_path.read_bytes()),
                "query_bytes": len(measured_query.encode()),
                "query_lines": len(measured_query.splitlines()),
                "operators": dict(sorted(operators.items())),
                **metadata,
            }
        )

    def write(self):
        (self.destination / "manifest.json").write_text(json.dumps(self.record, indent=2) + "\n")
