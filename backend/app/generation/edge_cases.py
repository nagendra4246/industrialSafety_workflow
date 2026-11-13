from __future__ import annotations

from datetime import timedelta
from typing import Any

from .dataset import GenerationConfig, Record, SyntheticDataset
from .ids import deterministic_uuid, incident_business_id
from .master_data import MasterData
from .clusters import find_alarm_clusters


EXPECTED_ISSUE_TYPES = (
    "acknowledgement_before_alarm",
    "impossible_interlock_state",
    "incident_without_triggering_context",
    "replay_duplicated_alarm_flood",
    "action_closed_without_verification",
)


def inject_known_edge_cases(
    master: MasterData,
    config: GenerationConfig,
    dataset: SyntheticDataset,
) -> None:
    alarms = dataset.tables["alarm_event"]
    first_alarm = alarms[0]
    acknowledgement = next(
        row
        for row in dataset.tables["acknowledgement"]
        if row["alarm_event_id"] == first_alarm["id"]
    )
    acknowledgement["acknowledged_at"] = first_alarm["event_at"] - timedelta(seconds=1)
    acknowledgement["created_at"] = acknowledgement["acknowledged_at"]
    acknowledgement["status"] = "acknowledged"
    dataset.ground_truth.append(
        _label(
            len(dataset.ground_truth) + 1,
            issue_type="acknowledgement_before_alarm",
            entity_type="acknowledgement",
            entity_id=acknowledgement["id"],
            business_id=acknowledgement["business_id"],
            related_entity_ids=[first_alarm["id"]],
            expected_decision="flag_acknowledgement_chronology",
            rationale="Acknowledgement timestamp precedes its referenced alarm event.",
        )
    )

    machine = master.machines[0]
    interlock = master.interlocks_by_machine_id[machine["id"]][0]
    opposite_state = {
        "open": "closed",
        "closed": "open",
        "energized": "deenergized",
        "deenergized": "energized",
    }[interlock["required_state"]]
    event_at = config.start_at + timedelta(seconds=int(config.days * 86400 * 0.8))
    state_event_id = deterministic_uuid(
        config.seed, "machine_state_event", "edge:impossible_interlock_state"
    )
    source_event_id = f"STATE-EDGE-{config.seed}-INTERLOCK"
    state_event = {
        "id": state_event_id,
        "machine_id": machine["id"],
        "interlock_id": interlock["id"],
        "state": "running",
        "interlock_state": opposite_state,
        "event_at": event_at,
        "source_event_id": source_event_id,
        "issue_type": None,
        "created_at": event_at,
    }
    dataset.tables["machine_state_event"].append(state_event)
    dataset.tables["machine_state_event"].sort(
        key=lambda row: (row["event_at"], str(row["id"]))
    )
    dataset.ground_truth.append(
        _label(
            len(dataset.ground_truth) + 1,
            issue_type="impossible_interlock_state",
            entity_type="machine_state_event",
            entity_id=state_event_id,
            business_id=source_event_id,
            related_entity_ids=[machine["id"], interlock["id"]],
            expected_decision="flag_interlock_state_mismatch",
            rationale=(
                f"Observed {opposite_state} conflicts with required state "
                f"{interlock['required_state']}."
            ),
        )
    )

    contextless_machine = master.machines[-1]
    incident_number = len(dataset.tables["incident"]) + 1
    incident_code = incident_business_id(config.start_at.year, incident_number)
    incident_id = deterministic_uuid(config.seed, "incident", incident_code)
    opened_at = config.start_at + timedelta(seconds=10)
    contextless_incident = {
        "id": incident_id,
        "business_id": incident_code,
        "machine_id": contextless_machine["id"],
        "opened_at": opened_at,
        "status": "open",
        "issue_type": "unsafe_condition",
        "created_at": opened_at,
        "updated_at": opened_at,
    }
    dataset.tables["incident"].append(contextless_incident)
    dataset.ground_truth.append(
        _label(
            len(dataset.ground_truth) + 1,
            issue_type="incident_without_triggering_context",
            entity_type="incident",
            entity_id=incident_id,
            business_id=incident_code,
            related_entity_ids=[contextless_machine["id"]],
            expected_decision="flag_incident_missing_context",
            rationale="The incident has no linked alarm or preceding unsafe machine state.",
        )
    )

    flood_candidates = find_alarm_clusters(alarms)
    if not flood_candidates:
        raise ValueError("generation did not create an alarm flood candidate")
    flood_cluster = max(
        flood_candidates,
        key=lambda cluster: (len(cluster), cluster[-1]["event_at"]),
    )
    replay_alarm = flood_cluster[0]
    original_delivery = next(
        row
        for row in dataset.replay_deliveries
        if row["external_event_id"] == replay_alarm["external_event_id"]
    )
    replay_delivery_id = str(
        deterministic_uuid(
            config.seed, "delivery-replay", replay_alarm["external_event_id"]
        )
    )
    dataset.replay_deliveries.append(
        {
            **original_delivery,
            "delivery_id": replay_delivery_id,
            "delivered_at": original_delivery["delivered_at"] + timedelta(seconds=30),
            "replay_of_delivery_id": original_delivery["delivery_id"],
        }
    )
    dataset.replay_deliveries.sort(
        key=lambda row: (row["delivered_at"], row["delivery_id"])
    )
    dataset.ground_truth.append(
        _label(
            len(dataset.ground_truth) + 1,
            issue_type="replay_duplicated_alarm_flood",
            entity_type="alarm_event",
            entity_id=replay_alarm["id"],
            business_id=replay_alarm["external_event_id"],
            related_entity_ids=[replay_alarm["machine_id"]],
            expected_decision="deduplicate_replayed_alarm_delivery",
            rationale=(
                "A raw delivery replays this alarm during a repeated-alarm flood; "
                "the persisted AlarmEvent remains unique by external_event_id."
            ),
        )
    )

    action = min(
        dataset.tables["corrective_action"],
        key=lambda row: (row["created_at"], str(row["id"])),
    )
    action["status"] = "closed"
    action["verification_result"] = "not_started"
    action["verification_evidence"] = None
    action["updated_at"] = action["completed_at"] + timedelta(hours=1, seconds=1)
    incident = next(
        row for row in dataset.tables["incident"] if row["id"] == action["incident_id"]
    )
    incident["status"] = "investigating"
    incident["updated_at"] = action["updated_at"]
    dataset.ground_truth.append(
        _label(
            len(dataset.ground_truth) + 1,
            issue_type="action_closed_without_verification",
            entity_type="corrective_action",
            entity_id=action["id"],
            business_id=action["business_id"],
            related_entity_ids=[incident["id"]],
            expected_decision="flag_unverified_action_closure",
            rationale="The action is closed while verification remains not_started.",
        )
    )
    dataset.ground_truth.sort(key=lambda row: row["label_id"])


def _label(
    sequence: int,
    *,
    issue_type: str,
    entity_type: str,
    entity_id: Any,
    business_id: str,
    related_entity_ids: list[Any],
    expected_decision: str,
    rationale: str,
) -> Record:
    return {
        "label_id": f"GT-{sequence:06d}",
        "issue_type": issue_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "business_id": business_id,
        "related_entity_ids": related_entity_ids,
        "expected_decision": expected_decision,
        "rationale": rationale,
    }
