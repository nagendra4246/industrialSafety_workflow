from __future__ import annotations

from datetime import timedelta
from typing import Any

from .dataset import GenerationConfig, Record, SyntheticDataset
from .ids import deterministic_uuid


INCIDENT_CONTEXT_WINDOW = timedelta(minutes=5)
UNSAFE_STATES = {"fault", "interlock_bypassed"}


def build_lifecycle_history(
    dataset: SyntheticDataset,
    operator_code_by_shift_id: dict[Any, str],
) -> list[Record]:
    config = dataset.config
    history: list[Record] = []

    for acknowledgement in dataset.tables["acknowledgement"]:
        status = acknowledgement["status"]
        history.append(
            _history_row(
                config,
                entity_type="acknowledgement",
                entity_id=acknowledgement["id"],
                transition="acknowledged_late" if status == "late" else "acknowledged",
                occurred_at=acknowledgement["acknowledged_at"],
                actor_reference=operator_code_by_shift_id[
                    acknowledgement["operator_shift_id"]
                ],
                payload={"status": status},
            )
        )

    actions_by_incident: dict[Any, list[Record]] = {}
    for action in dataset.tables["corrective_action"]:
        actions_by_incident.setdefault(action["incident_id"], []).append(action)
        history.extend(
            [
                _history_row(
                    config,
                    entity_type="corrective_action",
                    entity_id=action["id"],
                    transition="opened",
                    occurred_at=action["created_at"],
                    actor_reference=action["owner"],
                    payload={"status": "open"},
                ),
                _history_row(
                    config,
                    entity_type="corrective_action",
                    entity_id=action["id"],
                    transition="in_progress",
                    occurred_at=action["created_at"] + timedelta(minutes=15),
                    actor_reference=action["owner"],
                    payload={"status": "in_progress"},
                ),
                _history_row(
                    config,
                    entity_type="corrective_action",
                    entity_id=action["id"],
                    transition="completed",
                    occurred_at=action["completed_at"],
                    actor_reference=action["owner"],
                    payload={"status": "completed"},
                ),
            ]
        )
        if action["verification_result"] == "passed":
            history.append(
                _history_row(
                    config,
                    entity_type="corrective_action",
                    entity_id=action["id"],
                    transition="verified",
                    occurred_at=action["completed_at"] + timedelta(hours=1),
                    actor_reference=action["owner"],
                    payload={"verification_result": "passed"},
                )
            )
        if action["status"] == "closed":
            history.append(
                _history_row(
                    config,
                    entity_type="corrective_action",
                    entity_id=action["id"],
                    transition="closed",
                    occurred_at=action["updated_at"],
                    actor_reference=action["owner"],
                    payload={
                        "status": "closed",
                        "verification_result": action["verification_result"],
                    },
                )
            )

    alarms_by_id = {
        alarm["id"]: alarm for alarm in dataset.tables["alarm_event"]
    }
    states_by_machine: dict[Any, list[Record]] = {}
    for state_event in dataset.tables["machine_state_event"]:
        states_by_machine.setdefault(state_event["machine_id"], []).append(
            state_event
        )
    for state_events in states_by_machine.values():
        state_events.sort(key=lambda row: (row["event_at"], str(row["id"])))
    incident_alarm_ids: dict[Any, list[Any]] = {}
    for link in dataset.tables["incident_alarm_event"]:
        incident_alarm_ids.setdefault(link["incident_id"], []).append(
            link["alarm_event_id"]
        )

    for incident in dataset.tables["incident"]:
        linked_contexts = []
        for alarm_id in incident_alarm_ids.get(incident["id"], []):
            alarm = alarms_by_id[alarm_id]
            state_event = max(
                (
                    state
                    for state in states_by_machine.get(incident["machine_id"], [])
                    if state["state"] in UNSAFE_STATES
                    and timedelta(0)
                    <= alarm["event_at"] - state["event_at"]
                    <= INCIDENT_CONTEXT_WINDOW
                ),
                key=lambda row: (row["event_at"], str(row["id"])),
                default=None,
            )
            linked_contexts.append(
                {
                    "alarm_event_id": str(alarm_id),
                    "machine_state_event_id": (
                        str(state_event["id"]) if state_event is not None else None
                    ),
                }
            )
        history.append(
            _history_row(
                config,
                entity_type="incident",
                entity_id=incident["id"],
                transition="opened",
                occurred_at=incident["opened_at"],
                actor_reference="deterministic_rule",
                payload={"status": "open", "linked_contexts": linked_contexts},
            )
        )
        if incident["status"] != "open":
            related_actions = actions_by_incident.get(incident["id"], [])
            transition_time = max(
                (action["updated_at"] for action in related_actions),
                default=incident["updated_at"],
            )
            history.append(
                _history_row(
                    config,
                    entity_type="incident",
                    entity_id=incident["id"],
                    transition=incident["status"],
                    occurred_at=transition_time,
                    actor_reference="deterministic_rule",
                    payload={"status": incident["status"]},
                )
            )

    history.sort(key=lambda row: (row["occurred_at"], str(row["id"])))
    dataset.tables["lifecycle_history"] = history
    return history


def _history_row(
    config: GenerationConfig,
    *,
    entity_type: str,
    entity_id: Any,
    transition: str,
    occurred_at: Any,
    actor_reference: str | None,
    payload: dict[str, Any],
) -> Record:
    key = f"{entity_type}:{entity_id}:{transition}"
    return {
        "id": deterministic_uuid(config.seed, "lifecycle_history", key),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "transition": transition,
        "occurred_at": occurred_at,
        "actor_reference": actor_reference,
        "reason": None,
        "payload": payload,
        "created_at": occurred_at,
    }
