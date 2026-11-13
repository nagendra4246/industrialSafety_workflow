from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from app.exports import export_dataset

from .dataset import GenerationConfig, default_export_dir
from .generator import generate_dataset
from .validation import validate_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate, validate, and export a deterministic safety dataset."
    )
    parser.add_argument("--seed", type=int, default=GenerationConfig.seed)
    parser.add_argument("--plant-count", type=int, default=GenerationConfig.plant_count)
    parser.add_argument("--machine-count", type=int, default=GenerationConfig.machine_count)
    parser.add_argument(
        "--alarm-definition-count",
        type=int,
        default=GenerationConfig.alarm_definition_count,
    )
    parser.add_argument(
        "--alarm-event-count",
        type=int,
        default=GenerationConfig.alarm_event_count,
    )
    parser.add_argument("--days", type=int, default=GenerationConfig.days)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_export_dir(),
        help="directory for generated exports (default: data/exports)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = GenerationConfig(
        seed=args.seed,
        plant_count=args.plant_count,
        machine_count=args.machine_count,
        alarm_definition_count=args.alarm_definition_count,
        alarm_event_count=args.alarm_event_count,
        days=args.days,
    )
    dataset = generate_dataset(config)
    validation_summary = validate_dataset(dataset)
    manifest = export_dataset(dataset, args.output, validation_summary)
    passed = validation_summary["status"] == "passed"
    print(
        json.dumps(
            {
                "status": validation_summary["status"],
                "output_dir": str(args.output.resolve()),
                "row_counts": manifest["row_counts"],
                "checksums": manifest["checksums"],
                "validation_report": str(args.output / "validation_report.md"),
                "errors": validation_summary["errors"],
            },
            indent=2,
            sort_keys=True,
        ),
        file=sys.stdout if passed else sys.stderr,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
