from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import Enum, ForeignKeyConstraint, UniqueConstraint

from app.models import Base

from .clusters import find_alarm_clusters
from .dataset import Record, SyntheticDataset
from .edge_cases import EXPECTED_ISSUE_TYPES


SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SEVERITY_PRIORITY = {severity: rank for rank, severity in enumerate(SEVERITY_ORDER)}
UNSAFE_STATES = {"fault", "interlock_bypassed"}
INCIDENT_CONTEXT_WINDOW = timedelta(minutes=5)


def validate_dataset(
    dataset: SyntheticDataset,
    *,
    require_edge_cases: bool = True,
) -> dict[str, Any]:
    config = dataset.config
    counts = dataset.row_counts()
    errors: list[str] = []
    checks: dict[str, bool] = {}
    clusters = find_alarm_clusters(dataset.tables["alarm_event"])
    detections = _detect_edge_cases(dataset, clusters)
    edge_case_results, edge_cases_valid = _evaluate_edge_case_detections(
        dataset, detections, require_edge_cases
    )

    def record_check(name: str, valid: bool, error: str) -> None:
        checks[name] = valid
        if not valid:
            errors.append(error)

    expected_counts = {
        "plant": config.plant_count,
        "machine": config.machine_count,
        "safety_zone": config.plant_count * 3,
        "machine_safety_zone": config.machine_count * 2,
        "interlock": config.machine_count * 2,
        "alarm_definition": config.alarm_definition_count,
        "alarm_event": config.alarm_event_count,
    }
    record_check(
        "required_row_counts",
        all(counts[table] == expected for table, expected in expected_counts.items()),
        "one or more required table counts differ from the requested scale",
    )
    record_check(
        "table_shape",
        all(
            set(row) == {column.name for column in Base.metadata.tables[table].columns}
            for table, rows in dataset.tables.items()
            for row in rows
        ),
        "generated row fields do not match the PostgreSQL model",
    )

    primary_keys_valid, duplicate_primary_keys = _validate_primary_keys(dataset)
    record_check(
        "primary_key_uniqueness",
        primary_keys_valid,
        f"duplicate primary keys found: {duplicate_primary_keys[:3]}",
    )
    unique_valid, duplicate_unique_values = _validate_unique_constraints(dataset)
    record_check(
        "unique_constraints",
        unique_valid,
        f"unique constraint violations found: {duplicate_unique_values[:3]}",
    )
    foreign_keys_valid, missing_foreign_keys = _validate_foreign_keys(dataset)
    record_check(
        "foreign_key_integrity",
        foreign_keys_valid,
        f"foreign key references not found: {missing_foreign_keys[:3]}",
    )
    enum_valid, invalid_enums = _validate_enums(dataset)
    record_check(
        "enum_values",
        enum_valid,
        f"invalid enum values found: {invalid_enums[:3]}",
    )
    chronology_valid, chronology_errors = _validate_chronology(dataset)
    record_check(
        "chronological_ordering",
        chronology_valid,
        f"timeline violations found: {chronology_errors[:3]}",
    )
    record_check(
        "coherent_timeline",
        chronology_valid and edge_cases_valid,
        "timeline contains an unexplained chronology exception",
    )
    lifecycle_valid, lifecycle_errors = _validate_lifecycle_transitions(dataset)
    record_check(
        "lifecycle_transitions",
        lifecycle_valid,
        f"lifecycle violations found: {lifecycle_errors[:3]}",
    )
    ids_valid, invalid_business_ids = _validate_business_ids(dataset)
    record_check(
        "business_id_conventions",
        ids_valid,
        f"business identifier violations found: {invalid_business_ids[:3]}",
    )
    delivery_valid = _validate_replay_deliveries(
        dataset, require_duplicate=require_edge_cases
    )
    record_check(
        "replay_deduplication_fixture",
        delivery_valid,
        "raw delivery replay state does not match the requested validation mode",
    )
    burst_valid = bool(clusters)
    record_check(
        "alarm_flood_cluster_identification",
        burst_valid,
        "no repeated same-alarm cluster of at least three events was identified",
    )
    alarm_definitions = {
        row["id"]: row for row in dataset.tables["alarm_definition"]
    }
    nuisance_clusters = [
        _serialize_cluster(cluster, alarm_definitions)
        for cluster in clusters
        if alarm_definitions[cluster[0]["alarm_definition_id"]]["severity"]
        in {"low", "medium", "info"}
    ]
    nuisance_valid = bool(nuisance_clusters)
    record_check(
        "nuisance_cluster_identification",
        nuisance_valid,
        "no repeated low-or-medium-severity nuisance cluster was identified",
    )
    prioritization = _prioritize_alarm_events(dataset, alarm_definitions)
    record_check(
        "severe_alarm_prioritization",
        prioritization["passed"],
        "severe alarms were not ordered before lower-severity alarms or incident sources were not severe",
    )
    label_counts = Counter(row["issue_type"] for row in dataset.ground_truth)
    expected_labels = set(EXPECTED_ISSUE_TYPES) if require_edge_cases else set()
    labels_valid = (
        len(dataset.ground_truth) == len(expected_labels)
        and set(label_counts) == expected_labels
        and all(label_counts[issue_type] == 1 for issue_type in expected_labels)
    )
    record_check(
        "separate_ground_truth_coverage",
        labels_valid
        and all("ground_truth" not in row for rows in dataset.tables.values() for row in rows),
        "ground truth must match the validation mode and remain separate from source rows",
    )
    record_check(
        "labelled_edge_case_detection",
        edge_cases_valid,
        "detected edge cases do not match the five expected labels",
    )
    incidents_reconciled = _validate_incident_reconciliation(dataset)
    record_check(
        "incident_action_reconciliation",
        incidents_reconciled,
        "incident, linked alarm, or corrective-action relationships do not reconcile",
    )
    incident_traceability = incidents_reconciled and lifecycle_valid
    record_check(
        "incident_state_action_traceability",
        incident_traceability,
        "incident source-state, alarm, action, or lifecycle traceability is incomplete",
    )

    acknowledged_before_alarm_count = len(
        detections["acknowledgement_before_alarm"]
    )
    acceptance_metrics = {
        "coherent_timeline": {
            "passed": chronology_valid and edge_cases_valid,
            "known_acknowledgement_chronology_exceptions": (
                acknowledged_before_alarm_count
            ),
        },
        "severe_alarm_prioritization": prioritization,
        "nuisance_cluster_identification": {
            "passed": nuisance_valid,
            "cluster_count": len(nuisance_clusters),
            "clusters": nuisance_clusters,
        },
        "incident_traceability": {
            "passed": incident_traceability,
            "incident_count": len(dataset.tables["incident"]),
            "linked_alarm_count": len(dataset.tables["incident_alarm_event"]),
            "corrective_action_count": len(dataset.tables["corrective_action"]),
        },
    }

    return {
        "status": "passed" if not errors else "failed",
        "validation_mode": "full" if require_edge_cases else "baseline",
        "seed": config.seed,
        "parameters": config.as_dict(),
        "row_counts": counts,
        "checks": checks,
        "known_edge_case_counts": dict(sorted(label_counts.items())),
        "detected_edge_case_counts": {
            issue_type: len(rows)
            for issue_type, rows in sorted(detections.items())
        },
        "edge_case_results": edge_case_results,
        "acceptance_metrics": acceptance_metrics,
        "errors": errors,
    }


def _validate_primary_keys(dataset: SyntheticDataset) -> tuple[bool, list[str]]:
    duplicates: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        key_columns = [column.name for column in table.primary_key.columns]
        seen: set[tuple[Any, ...]] = set()
        for row in dataset.tables[table_name]:
            key = tuple(row[name] for name in key_columns)
            if key in seen:
                duplicates.append(f"{table_name}:{key}")
            seen.add(key)
    return not duplicates, duplicates


def _validate_unique_constraints(
    dataset: SyntheticDataset,
) -> tuple[bool, list[str]]:
    duplicates: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        unique_keys = [
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        for key_columns in unique_keys:
            seen: set[tuple[Any, ...]] = set()
            for row in dataset.tables[table_name]:
                values = tuple(row[name] for name in key_columns)
                if any(value is None for value in values):
                    continue
                if values in seen:
                    duplicates.append(f"{table_name}:{key_columns}:{values}")
                seen.add(values)
    return not duplicates, duplicates


def _validate_foreign_keys(dataset: SyntheticDataset) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            elements = list(constraint.elements)
            local_columns = tuple(element.parent.name for element in elements)
            remote_columns = tuple(element.column.name for element in elements)
            remote_table = elements[0].column.table.name
            targets = {
                tuple(row[column] for column in remote_columns)
                for row in dataset.tables[remote_table]
            }
            for row in dataset.tables[table_name]:
                key = tuple(row[column] for column in local_columns)
                if any(value is None for value in key):
                    continue
                if key not in targets:
                    missing.append(f"{table_name}:{local_columns}:{key}")
    return not missing, missing


def _validate_enums(dataset: SyntheticDataset) -> tuple[bool, list[str]]:
    invalid: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            if not isinstance(column.type, Enum):
                continue
            allowed = set(column.type.enums)
            for row in dataset.tables[table_name]:
                if row[column.name] is not None and row[column.name] not in allowed:
                    invalid.append(f"{table_name}.{column.name}={row[column.name]!r}")
    return not invalid, invalid


def _validate_chronology(dataset: SyntheticDataset) -> tuple[bool, list[str]]:
    errors: list[str] = []
    config = dataset.config
    for table_name, rows in dataset.tables.items():
        if "effective_from" in Base.metadata.tables[table_name].columns:
            for row in rows:
                end_at = row.get("effective_to")
                if end_at is not None and end_at <= row["effective_from"]:
                    errors.append(f"{table_name}:{row['id']}:invalid_effective_dates")
    for row in dataset.tables["operator_shift"]:
        if row["ends_at"] <= row["starts_at"]:
            errors.append(f"operator_shift:{row['id']}:invalid_shift_time")
    alarms = {row["id"]: row for row in dataset.tables["alarm_event"]}
    definitions = {
        row["id"]: row for row in dataset.tables["alarm_definition"]
    }
    for row in dataset.tables["alarm_event"]:
        if not (config.start_at <= row["event_at"] < config.end_at):
            errors.append(f"alarm_event:{row['id']}:outside_generation_window")
        if row["received_at"] < row["event_at"]:
            errors.append(f"alarm_event:{row['id']}:received_before_event")
        if row["cleared_at"] is not None and row["cleared_at"] < row["event_at"]:
            errors.append(f"alarm_event:{row['id']}:cleared_before_event")
    for row in dataset.tables["machine_state_event"]:
        if not (config.start_at <= row["event_at"] < config.end_at):
            errors.append(f"machine_state_event:{row['id']}:outside_generation_window")
    event_times = [row["event_at"] for row in dataset.tables["alarm_event"]]
    if event_times != sorted(event_times):
        errors.append("alarm_event:rows_not_chronologically_ordered")
    state_times = [row["event_at"] for row in dataset.tables["machine_state_event"]]
    if state_times != sorted(state_times):
        errors.append("machine_state_event:rows_not_chronologically_ordered")
    history_times = [row["occurred_at"] for row in dataset.tables["lifecycle_history"]]
    if history_times != sorted(history_times):
        errors.append("lifecycle_history:rows_not_chronologically_ordered")
    if event_times and (
        event_times[0] != config.start_at + timedelta(seconds=60)
        or event_times[-1] != config.end_at - timedelta(seconds=1)
    ):
        errors.append("alarm_event:time_span_does_not_cover_requested_window")

    shifts = {row["id"]: row for row in dataset.tables["operator_shift"]}
    for row in dataset.tables["acknowledgement"]:
        alarm = alarms[row["alarm_event_id"]]
        definition = definitions[alarm["alarm_definition_id"]]
        shift = shifts[row["operator_shift_id"]]
        if not (shift["starts_at"] <= row["acknowledged_at"] < shift["ends_at"]):
            errors.append(f"acknowledgement:{row['id']}:outside_operator_shift")
        elapsed = (row["acknowledged_at"] - alarm["event_at"]).total_seconds()
        expected_status = (
            "late" if elapsed > definition["ack_sla_seconds"] else "acknowledged"
        )
        if row["status"] != expected_status:
            errors.append(f"acknowledgement:{row['id']}:status_not_derived_from_timeline")

    entity_tables = {
        "plant": "plant",
        "machine": "machine",
        "safety_zone": "safety_zone",
        "interlock": "interlock",
        "alarm_definition": "alarm_definition",
        "alarm_event": "alarm_event",
        "operator_shift": "operator_shift",
        "acknowledgement": "acknowledgement",
        "incident": "incident",
        "corrective_action": "corrective_action",
        "machine_state_event": "machine_state_event",
    }
    entity_ids = {
        kind: {row["id"] for row in dataset.tables[table_name]}
        for kind, table_name in entity_tables.items()
    }
    for row in dataset.tables["lifecycle_history"]:
        if row["entity_id"] not in entity_ids[row["entity_type"]]:
            errors.append(f"lifecycle_history:{row['id']}:unknown_entity_reference")
    return not errors, errors


def _validate_business_ids(dataset: SyntheticDataset) -> tuple[bool, list[str]]:
    errors: list[str] = []
    plants = {row["id"]: row for row in dataset.tables["plant"]}
    machines = {row["id"]: row for row in dataset.tables["machine"]}
    zones_by_plant: dict[Any, list[Record]] = {}
    for zone in dataset.tables["safety_zone"]:
        zones_by_plant.setdefault(zone["plant_id"], []).append(zone)
    for plant_id, zones in zones_by_plant.items():
        plant_code = plants[plant_id]["plant_code"]
        for counter, zone in enumerate(
            sorted(zones, key=lambda row: row["zone_code"]), start=1
        ):
            if zone["business_id"] != f"SAFE-{plant_code}-{counter:03d}":
                errors.append(f"safety_zone:{zone['id']}:invalid_business_id")

    counters_by_machine: dict[Any, int] = {}
    for alarm in dataset.tables["alarm_definition"]:
        machine = machines[alarm["machine_id"]]
        counters_by_machine[alarm["machine_id"]] = (
            counters_by_machine.get(alarm["machine_id"], 0) + 1
        )
        expected = (
            f"ALM-{machine['machine_code']}-"
            f"{counters_by_machine[alarm['machine_id']]:06d}"
        )
        if alarm["business_id"] != expected:
            errors.append(f"alarm_definition:{alarm['id']}:invalid_business_id")

    for counter, incident in enumerate(dataset.tables["incident"], start=1):
        expected = f"INC-{incident['opened_at'].year}-{counter:06d}"
        if incident["business_id"] != expected:
            errors.append(f"incident:{incident['id']}:invalid_business_id")
    alarms = {row["id"]: row for row in dataset.tables["alarm_event"]}
    for acknowledgement in dataset.tables["acknowledgement"]:
        expected = f"ACK-{alarms[acknowledgement['alarm_event_id']]['external_event_id']}-01"
        if acknowledgement["business_id"] != expected:
            errors.append(f"acknowledgement:{acknowledgement['id']}:invalid_business_id")
    incidents = {row["id"]: row for row in dataset.tables["incident"]}
    for action in dataset.tables["corrective_action"]:
        expected = f"ACT-{incidents[action['incident_id']]['business_id']}-01"
        if action["business_id"] != expected:
            errors.append(f"corrective_action:{action['id']}:invalid_business_id")
    return not errors, errors


def _validate_lifecycle_transitions(
    dataset: SyntheticDataset,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    histories: dict[tuple[str, Any], list[Record]] = {}
    for row in dataset.tables["lifecycle_history"]:
        histories.setdefault((row["entity_type"], row["entity_id"]), []).append(row)
    for rows in histories.values():
        rows.sort(key=lambda row: (row["occurred_at"], str(row["id"])))

    for acknowledgement in dataset.tables["acknowledgement"]:
        key = ("acknowledgement", acknowledgement["id"])
        rows = histories.get(key, [])
        expected = (
            "acknowledged_late"
            if acknowledgement["status"] == "late"
            else "acknowledged"
        )
        if len(rows) != 1 or rows[0]["transition"] != expected:
            errors.append(f"acknowledgement:{acknowledgement['id']}:invalid_lifecycle")
        elif rows[0]["occurred_at"] != acknowledgement["acknowledged_at"]:
            errors.append(f"acknowledgement:{acknowledgement['id']}:lifecycle_time_mismatch")

    for incident in dataset.tables["incident"]:
        rows = histories.get(("incident", incident["id"]), [])
        if not rows or rows[0]["transition"] != "opened":
            errors.append(f"incident:{incident['id']}:missing_open_transition")
        elif rows[0]["occurred_at"] != incident["opened_at"]:
            errors.append(f"incident:{incident['id']}:open_time_mismatch")
        if incident["status"] == "open":
            if len(rows) != 1:
                errors.append(f"incident:{incident['id']}:unexpected_lifecycle_transition")
        elif not rows or rows[-1]["transition"] != incident["status"]:
            errors.append(f"incident:{incident['id']}:status_transition_mismatch")

    for action in dataset.tables["corrective_action"]:
        rows = histories.get(("corrective_action", action["id"]), [])
        transitions = [row["transition"] for row in rows]
        required = ["opened", "in_progress", "completed"]
        if transitions[:3] != required:
            errors.append(f"corrective_action:{action['id']}:missing_required_transitions")
            continue
        if action["status"] == "closed" and transitions[-1] != "closed":
            errors.append(f"corrective_action:{action['id']}:missing_closed_transition")
        verified = "verified" in transitions
        if action["verification_result"] == "passed":
            closed_index = (
                transitions.index("closed") if "closed" in transitions else None
            )
            if (
                not verified
                or closed_index is None
                or transitions.index("verified") > closed_index
            ):
                errors.append(f"corrective_action:{action['id']}:verification_order_invalid")
        elif verified:
            errors.append(f"corrective_action:{action['id']}:unexpected_verified_transition")
        transition_times = [row["occurred_at"] for row in rows]
        if transition_times != sorted(transition_times):
            errors.append(f"corrective_action:{action['id']}:transitions_out_of_order")
    return not errors, errors


def _validate_replay_deliveries(
    dataset: SyntheticDataset,
    *,
    require_duplicate: bool,
) -> bool:
    deliveries = dataset.replay_deliveries
    expected_delivery_count = (
        dataset.config.alarm_event_count + 1
        if require_duplicate
        else dataset.config.alarm_event_count
    )
    if len(deliveries) != expected_delivery_count:
        return False
    if len({row["delivery_id"] for row in deliveries}) != len(deliveries):
        return False
    deliveries_by_id = {row["delivery_id"]: row for row in deliveries}
    replay_rows = [
        row for row in deliveries if row["replay_of_delivery_id"] is not None
    ]
    if len(replay_rows) != (1 if require_duplicate else 0):
        return False
    for row in replay_rows:
        parent = deliveries_by_id.get(row["replay_of_delivery_id"])
        if (
            parent is None
            or parent["replay_of_delivery_id"] is not None
            or parent["external_event_id"] != row["external_event_id"]
            or row["delivered_at"] <= parent["delivered_at"]
        ):
            return False
    counts = Counter(row["external_event_id"] for row in deliveries)
    duplicates = [event_id for event_id, count in counts.items() if count > 1]
    if len(duplicates) != (1 if require_duplicate else 0):
        return False
    if any(counts[event_id] != 2 for event_id in duplicates):
        return False
    alarm_event_ids = {
        row["external_event_id"] for row in dataset.tables["alarm_event"]
    }
    return (
        set(counts) == alarm_event_ids
        and len(alarm_event_ids) == len(dataset.tables["alarm_event"])
    )


def _detect_edge_cases(
    dataset: SyntheticDataset,
    clusters: list[list[Record]],
) -> dict[str, list[dict[str, Any]]]:
    detections = {issue_type: [] for issue_type in EXPECTED_ISSUE_TYPES}
    alarm_by_id = {row["id"]: row for row in dataset.tables["alarm_event"]}
    alarm_by_external_id = {
        row["external_event_id"]: row for row in dataset.tables["alarm_event"]
    }
    definitions = {
        row["id"]: row for row in dataset.tables["alarm_definition"]
    }
    interlocks = {row["id"]: row for row in dataset.tables["interlock"]}
    states_by_machine: dict[Any, list[Record]] = {}
    for row in dataset.tables["machine_state_event"]:
        states_by_machine.setdefault(row["machine_id"], []).append(row)
    links_by_incident: dict[Any, list[Any]] = {}
    for row in dataset.tables["incident_alarm_event"]:
        links_by_incident.setdefault(row["incident_id"], []).append(
            row["alarm_event_id"]
        )

    for acknowledgement in dataset.tables["acknowledgement"]:
        alarm = alarm_by_id.get(acknowledgement["alarm_event_id"])
        if alarm and acknowledgement["acknowledged_at"] < alarm["event_at"]:
            detections["acknowledgement_before_alarm"].append(
                {
                    "entity_id": str(acknowledgement["id"]),
                    "evidence": {
                        "acknowledged_at": acknowledgement["acknowledged_at"].isoformat(),
                        "alarm_event_at": alarm["event_at"].isoformat(),
                    },
                }
            )

    for state_event in dataset.tables["machine_state_event"]:
        interlock = interlocks.get(state_event["interlock_id"])
        if (
            interlock
            and state_event["interlock_state"] is not None
            and state_event["interlock_state"] != interlock["required_state"]
        ):
            detections["impossible_interlock_state"].append(
                {
                    "entity_id": str(state_event["id"]),
                    "evidence": {
                        "observed_state": state_event["interlock_state"],
                        "required_state": interlock["required_state"],
                    },
                }
            )

    for incident in dataset.tables["incident"]:
        linked_alarms = [
            alarm_by_id[alarm_id]
            for alarm_id in links_by_incident.get(incident["id"], [])
            if alarm_id in alarm_by_id
        ]
        if not _has_triggering_context(
            incident, linked_alarms, definitions, states_by_machine
        ):
            detections["incident_without_triggering_context"].append(
                {
                    "entity_id": str(incident["id"]),
                    "evidence": {
                        "linked_alarm_count": len(linked_alarms),
                        "machine_id": str(incident["machine_id"]),
                    },
                }
            )

    cluster_size_by_event_id = {
        event["id"]: len(cluster)
        for cluster in clusters
        for event in cluster
    }
    deliveries_by_external_id: dict[str, list[Record]] = {}
    for delivery in dataset.replay_deliveries:
        deliveries_by_external_id.setdefault(
            delivery["external_event_id"], []
        ).append(delivery)
    for external_event_id, deliveries in deliveries_by_external_id.items():
        alarm = alarm_by_external_id.get(external_event_id)
        if (
            len(deliveries) > 1
            and alarm is not None
            and alarm["id"] in cluster_size_by_event_id
        ):
            detections["replay_duplicated_alarm_flood"].append(
                {
                    "entity_id": str(alarm["id"]),
                    "evidence": {
                        "external_event_id": external_event_id,
                        "delivery_count": len(deliveries),
                        "alarm_cluster_size": cluster_size_by_event_id[alarm["id"]],
                    },
                }
            )

    for action in dataset.tables["corrective_action"]:
        if action["status"] == "closed" and action["verification_result"] != "passed":
            detections["action_closed_without_verification"].append(
                {
                    "entity_id": str(action["id"]),
                    "evidence": {
                        "status": action["status"],
                        "verification_result": action["verification_result"],
                    },
                }
            )
    return detections


def _has_triggering_context(
    incident: Record,
    linked_alarms: list[Record],
    definitions: dict[Any, Record],
    states_by_machine: dict[Any, list[Record]],
) -> bool:
    for alarm in linked_alarms:
        if alarm["machine_id"] != incident["machine_id"]:
            continue
        if definitions[alarm["alarm_definition_id"]]["severity"] not in {
            "high",
            "critical",
        }:
            continue
        incident_delay = incident["opened_at"] - alarm["event_at"]
        if not timedelta(0) <= incident_delay <= INCIDENT_CONTEXT_WINDOW:
            continue
        if any(
            state["state"] in UNSAFE_STATES
            and timedelta(0) <= alarm["event_at"] - state["event_at"]
            <= INCIDENT_CONTEXT_WINDOW
            for state in states_by_machine.get(incident["machine_id"], [])
        ):
            return True
    return False


def _evaluate_edge_case_detections(
    dataset: SyntheticDataset,
    detections: dict[str, list[dict[str, Any]]],
    require_edge_cases: bool,
) -> tuple[list[dict[str, Any]], bool]:
    labels_by_issue: dict[str, list[Record]] = {}
    for label in dataset.ground_truth:
        labels_by_issue.setdefault(label["issue_type"], []).append(label)

    results: list[dict[str, Any]] = []
    all_valid = True
    for issue_type in EXPECTED_ISSUE_TYPES:
        findings = detections[issue_type]
        labels = labels_by_issue.get(issue_type, [])
        if require_edge_cases:
            matched = (
                len(findings) == 1
                and len(labels) == 1
                and findings[0]["entity_id"] == str(labels[0]["entity_id"])
            )
        else:
            matched = not findings and not labels
        all_valid = all_valid and matched
        results.append(
            {
                "issue_type": issue_type,
                "expected": require_edge_cases,
                "detected": bool(findings),
                "detection_count": len(findings),
                "label_count": len(labels),
                "label_match": matched,
                "entity_id": (
                    findings[0]["entity_id"] if len(findings) == 1 else None
                ),
                "expected_decision": (
                    labels[0]["expected_decision"] if len(labels) == 1 else None
                ),
                "evidence": [finding["evidence"] for finding in findings],
            }
        )
    return results, all_valid


def _serialize_cluster(
    cluster: list[Record],
    definitions: dict[Any, Record],
) -> dict[str, Any]:
    definition = definitions[cluster[0]["alarm_definition_id"]]
    return {
        "machine_id": str(cluster[0]["machine_id"]),
        "alarm_definition_id": str(cluster[0]["alarm_definition_id"]),
        "severity": definition["severity"],
        "event_count": len(cluster),
        "starts_at": cluster[0]["event_at"].isoformat(),
        "ends_at": cluster[-1]["event_at"].isoformat(),
    }


def _prioritize_alarm_events(
    dataset: SyntheticDataset,
    definitions: dict[Any, Record],
) -> dict[str, Any]:
    alarms = dataset.tables["alarm_event"]
    severity_by_alarm_id = {
        alarm["id"]: definitions[alarm["alarm_definition_id"]]["severity"]
        for alarm in alarms
    }
    ordered = sorted(
        alarms,
        key=lambda alarm: (
            SEVERITY_PRIORITY.get(severity_by_alarm_id[alarm["id"]], len(SEVERITY_ORDER)),
            alarm["event_at"],
            str(alarm["id"]),
        ),
    )
    counts = {
        severity: sum(
            severity_by_alarm_id[alarm["id"]] == severity for alarm in alarms
        )
        for severity in SEVERITY_ORDER
    }
    severe_count = counts["critical"] + counts["high"]
    severe_prefix = all(
        severity_by_alarm_id[alarm["id"]] in {"critical", "high"}
        for alarm in ordered[:severe_count]
    )
    linked_alarm_ids = {
        row["alarm_event_id"] for row in dataset.tables["incident_alarm_event"]
    }
    incident_sources_are_severe = all(
        severity_by_alarm_id[alarm_id] in {"critical", "high"}
        for alarm_id in linked_alarm_ids
        if alarm_id in severity_by_alarm_id
    )
    return {
        "passed": bool(ordered) and severe_count > 0 and severe_prefix and incident_sources_are_severe,
        "severity_order": list(SEVERITY_ORDER),
        "severity_counts": counts,
        "severe_alarm_count": severe_count,
        "incident_source_alarm_count": len(linked_alarm_ids),
        "top_priority_alarm_events": [
            {
                "alarm_event_id": str(alarm["id"]),
                "severity": severity_by_alarm_id[alarm["id"]],
                "event_at": alarm["event_at"].isoformat(),
            }
            for alarm in ordered[:10]
        ],
    }


def _validate_incident_reconciliation(dataset: SyntheticDataset) -> bool:
    incidents = dataset.tables["incident"]
    actions = dataset.tables["corrective_action"]
    links_by_incident: dict[Any, list[Any]] = {}
    for row in dataset.tables["incident_alarm_event"]:
        links_by_incident.setdefault(row["incident_id"], []).append(row["alarm_event_id"])
    event_by_id = {row["id"]: row for row in dataset.tables["alarm_event"]}
    definition_by_id = {
        row["id"]: row for row in dataset.tables["alarm_definition"]
    }
    state_events_by_machine: dict[Any, list[Record]] = {}
    for row in dataset.tables["machine_state_event"]:
        state_events_by_machine.setdefault(row["machine_id"], []).append(row)
    action_incident_ids = {row["incident_id"] for row in actions}
    lifecycle_by_incident: dict[Any, list[Record]] = {}
    for row in dataset.tables["lifecycle_history"]:
        if row["entity_type"] == "incident":
            lifecycle_by_incident.setdefault(row["entity_id"], []).append(row)
    incident_alarm_pairs = {
        (incident_id, event_id)
        for incident_id, event_ids in links_by_incident.items()
        for event_id in event_ids
    }

    for incident in incidents:
        linked_events = [
            event_by_id[event_id]
            for event_id in links_by_incident.get(incident["id"], [])
        ]
        if not _has_triggering_context(
            incident,
            linked_events,
            definition_by_id,
            state_events_by_machine,
        ):
            if linked_events or incident["id"] in action_incident_ids:
                return False
            continue

        if not linked_events or incident["id"] not in action_incident_ids:
            return False
        incident_history = lifecycle_by_incident.get(incident["id"], [])
        if not incident_history or incident_history[0]["transition"] != "opened":
            return False
        linked_contexts = incident_history[0]["payload"].get(
            "linked_contexts", []
        )
        for event in linked_events:
            if event["machine_id"] != incident["machine_id"]:
                return False
            if definition_by_id[event["alarm_definition_id"]]["severity"] not in {
                "high",
                "critical",
            }:
                return False
            if not any(
                state_event["state"] in {"fault", "interlock_bypassed"}
                and timedelta(0)
                <= event["event_at"] - state_event["event_at"]
                <= timedelta(minutes=5)
                for state_event in state_events_by_machine.get(incident["machine_id"], ())
            ):
                return False
            valid_state_ids = {
                str(state_event["id"])
                for state_event in state_events_by_machine.get(
                    incident["machine_id"], ()
                )
                if state_event["state"] in {"fault", "interlock_bypassed"}
                and timedelta(0)
                <= event["event_at"] - state_event["event_at"]
                <= timedelta(minutes=5)
            }
            if not any(
                context.get("alarm_event_id") == str(event["id"])
                and context.get("machine_state_event_id") in valid_state_ids
                for context in linked_contexts
                if isinstance(context, dict)
            ):
                return False

    return len(incident_alarm_pairs) == len(actions)
