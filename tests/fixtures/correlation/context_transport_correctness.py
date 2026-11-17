"""Freeze the focused #53 correctness matrix through public conversion."""

import argparse
import json
import subprocess
from pathlib import Path

from tests.fixtures.correlation.relational_context import fixtures
from tests.fixtures.correlation.runtime import manifest

COVERAGE = (
    "direct",
    "shared",
    "multilevel",
    "callback",
    "display-boundaries",
    "hidden-parent-values",
    "cross-field-relationships",
    "stable-consumer-identities",
)


def freeze(destination: Path, strategy="production-summary-evidence-union"):
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError("frozen manifest already exists; choose a new output directory")
    for name, query in fixtures().items():
        (destination / name).write_text(query)
    record = manifest(destination)
    record.update(
        revision=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        strategy=strategy,
        coverage=list(COVERAGE),
        oracle="independent empty-multiset diagnostics generated through public collection conversion",
    )
    manifest_path.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strategy", default="production-summary-evidence-union")
    args = parser.parse_args()
    freeze(args.output, args.strategy)
