from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON

from app.models import Base

from app.generation.dataset import TABLE_ORDER, Record, SyntheticDataset


GENERATOR_VERSION = "0.1.0"
SQL_BATCH_SIZE = 1000


def export_dataset(
    dataset: SyntheticDataset,
    output_dir: Path,
    validation_summary: dict[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    row_counts = dataset.row_counts()
    extra_counts = {
        "replay_deliveries": len(dataset.replay_deliveries),
        "ground_truth": len(dataset.ground_truth),
    }

    for table_name in TABLE_ORDER:
        rows = dataset.tables[table_name]
        columns = [column.name for column in Base.metadata.tables[table_name].columns]
        _write_csv(output_dir / f"{table_name}.csv", rows, columns)
        _write_json(output_dir / f"{table_name}.json", rows)
    _write_csv(
        output_dir / "replay_deliveries.csv",
        dataset.replay_deliveries,
        _row_columns(dataset.replay_deliveries),
    )
    _write_json(output_dir / "replay_deliveries.json", dataset.replay_deliveries)
    _write_csv(
        output_dir / "ground_truth.csv",
        dataset.ground_truth,
        _row_columns(dataset.ground_truth),
    )
    _write_json(output_dir / "ground_truth.json", dataset.ground_truth)
    _write_sql(output_dir / "seed.sql", dataset)
    _write_validation_summary(output_dir / "validation_summary.json", validation_summary)
    _write_human_validation_report(
        output_dir / "validation_report.md", validation_summary
    )
    _write_readme(output_dir / "README.md", dataset, row_counts, extra_counts)
    _write_data_dictionary(output_dir / "data-dictionary.md")

    checksums = {
        path.name: {
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "manifest_version": 1,
        "generator": "industrial-safety-workflow.synthetic",
        "generator_version": GENERATOR_VERSION,
        "parameters": dataset.config.as_dict(),
        "source_manifest": {
            "source_type": "synthetic",
            "seed": dataset.config.seed,
            "external_sources": [],
            "transformations": [
                "Deterministic master and event generation",
                "Documented rule-derived acknowledgements, incidents, and corrective actions",
                "Five separately labelled edge-case injections",
            ],
        },
        "row_counts": {**row_counts, **extra_counts},
        "checksums": checksums,
        "manifest_checksum_note": "manifest.json is excluded from its own checksum list",
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def _row_columns(rows: list[Record]) -> list[str]:
    return list(rows[0]) if rows else []


def _write_csv(path: Path, rows: list[Record], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(_json_value(value), output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _write_validation_summary(path: Path, summary: dict[str, Any]) -> None:
    _write_json(path, summary)


def _write_human_validation_report(path: Path, summary: dict[str, Any]) -> None:
    metrics = summary["acceptance_metrics"]
    lines = [
        "# Synthetic Dataset Validation Report",
        "",
        f"- **Status:** {summary['status'].upper()}",
        f"- **Validation mode:** {summary['validation_mode']}",
        f"- **Seed:** `{summary['seed']}`",
        f"- **Event window:** `{summary['parameters']['start_at']}` to "
        f"`{summary['parameters']['end_at_exclusive']}` (end exclusive)",
        "",
        "## Structural and workflow checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    lines.extend(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in summary["checks"].items()
    )
    lines.extend(
        [
            "",
            "## Labelled edge-case detections",
            "",
            "| Edge case | Detected | Detection count | Label matched | Expected decision |",
            "|---|---:|---:|---:|---|",
        ]
    )
    lines.extend(
        "| `{issue_type}` | {detected} | {detection_count} | {label_match} | "
        "`{decision}` |".format(
            issue_type=result["issue_type"],
            detected="yes" if result["detected"] else "no",
            detection_count=result["detection_count"],
            label_match="yes" if result["label_match"] else "no",
            decision=result["expected_decision"] or "n/a",
        )
        for result in summary["edge_case_results"]
    )
    lines.extend(
        [
            "",
            "Detected evidence is derived from source records. Ground-truth labels are "
            "used only to compare expected cases with detector results.",
            "",
            "## Acceptance metrics",
            "",
            f"- Coherent timeline, with labelled chronology exceptions reported: "
            f"**{'PASS' if metrics['coherent_timeline']['passed'] else 'FAIL'}** "
            f"({metrics['coherent_timeline']['known_acknowledgement_chronology_exceptions']} "
            "acknowledgement-before-alarm exception(s)).",
            f"- Severe-alarm prioritization: "
            f"**{'PASS' if metrics['severe_alarm_prioritization']['passed'] else 'FAIL'}** "
            f"(severity order: "
            f"`{' > '.join(metrics['severe_alarm_prioritization']['severity_order'])}`).",
            f"- Incident state/alarm/action traceability: "
            f"**{'PASS' if metrics['incident_traceability']['passed'] else 'FAIL'}** "
            f"({metrics['incident_traceability']['incident_count']} incidents, "
            f"{metrics['incident_traceability']['linked_alarm_count']} alarm links, "
            f"{metrics['incident_traceability']['corrective_action_count']} actions).",
            "",
            "### Nuisance clusters identified",
            "",
        ]
    )
    nuisance_clusters = metrics["nuisance_cluster_identification"]["clusters"]
    if nuisance_clusters:
        lines.extend(
            [
                "| Severity | Event count | Machine | Alarm definition | Start | End |",
                "|---|---:|---|---|---|---|",
            ]
        )
        lines.extend(
            "| `{severity}` | {event_count} | `{machine_id}` | "
            "`{alarm_definition_id}` | `{starts_at}` | `{ends_at}` |".format(
                **cluster
            )
            for cluster in nuisance_clusters
        )
    else:
        lines.append("No qualifying nuisance cluster was found.")
    lines.extend(["", "## Validation errors", ""])
    if summary["errors"]:
        lines.extend(f"- {error}" for error in summary["errors"])
    else:
        lines.append("None.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _write_readme(
    path: Path,
    dataset: SyntheticDataset,
    row_counts: dict[str, int],
    extra_counts: dict[str, int],
) -> None:
    config = dataset.config
    counts = {**row_counts, **extra_counts}
    lines = [
        "# Synthetic Industrial Safety Workflow Dataset",
        "",
        f"- Seed: `{config.seed}`",
        f"- Event window: `{config.start_at.isoformat()}` through "
        f"`{config.end_at.isoformat()}` (end exclusive)",
        "- Source: deterministic synthetic data; no external datasets used.",
        "- `ground_truth.csv` and `ground_truth.json` are labels only. They are excluded "
        "from `seed.sql` and from all source-table rows.",
        "- `replay_deliveries.*` preserves a raw replay duplicate; persisted alarm events "
        "remain unique by `external_event_id`.",
        "",
        "## Generator command",
        "",
        "```sh",
        "python -m app.generation.cli --seed "
        f"{config.seed} --plant-count {config.plant_count} "
        f"--machine-count {config.machine_count} "
        f"--alarm-definition-count {config.alarm_definition_count} "
        f"--alarm-event-count {config.alarm_event_count} --days {config.days}",
        "```",
        "",
        "## Row counts",
        "",
        "| Dataset | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| `{table}` | {count} |" for table, count in counts.items())
    lines.extend(
        [
            "",
            "Each schema table is exported as CSV and JSON. `seed.sql` contains schema-table "
            "records only. `data-dictionary.md` accompanies the export. `manifest.json` "
            "lists SHA-256 checksums for every other output; machine-readable validation "
            "is in `validation_summary.json` and its human-readable counterpart is "
            "`validation_report.md`. The manifest is excluded from its own checksum list.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _write_data_dictionary(path: Path) -> None:
    candidates = (
        Path.cwd() / "docs" / "data-dictionary.md",
        Path.cwd().parent / "docs" / "data-dictionary.md",
        Path(__file__).resolve().parents[3] / "docs" / "data-dictionary.md",
    )
    source = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source is None:
        raise FileNotFoundError(
            "docs/data-dictionary.md is required to produce a complete export"
        )
    path.write_bytes(source.read_bytes())


def _write_sql(path: Path, dataset: SyntheticDataset) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("-- Deterministic generated seed data; ground truth is intentionally excluded.\n")
        output.write("BEGIN;\n\n")
        for table_name in TABLE_ORDER:
            table = Base.metadata.tables[table_name]
            rows = dataset.tables[table_name]
            if not rows:
                continue
            columns = list(table.columns)
            quoted_columns = ", ".join(_quote_identifier(column.name) for column in columns)
            prefix = (
                f"INSERT INTO {_quote_identifier(table_name)} ({quoted_columns}) VALUES\n"
            )
            for offset in range(0, len(rows), SQL_BATCH_SIZE):
                batch = rows[offset : offset + SQL_BATCH_SIZE]
                values = [
                    "("
                    + ", ".join(
                        _sql_value(row[column.name], column)
                        for column in columns
                    )
                    + ")"
                    for row in batch
                ]
                output.write(prefix)
                output.write(",\n".join(values))
                output.write(";\n\n")
        output.write("COMMIT;\n")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_value(value: Any, column: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, uuid.UUID):
        return _sql_string(str(value))
    if isinstance(value, (datetime, date)):
        return _sql_string(_isoformat(value))
    if isinstance(column.type, JSON):
        document = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True)
        return f"{_sql_string(document)}::jsonb"
    return _sql_string(str(value))


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return _isoformat(value)
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _isoformat(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _isoformat(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value.isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
