from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from .dataset import GenerationConfig, Record, SyntheticDataset
from .ids import (
    acknowledgement_business_id,
    corrective_action_business_id,
    deterministic_uuid,
    incident_business_id,
)
from .master_data import MasterData


ALARM_FLOOD_SIZE = 200
NUISANCE_CLUSTER_SIZE = 50
INCIDENT_CONTEXT_WINDOW = timedelta(minutes=5)


def build_baseline_events(master: MasterData, config: GenerationConfig) -> SyntheticDataset:
    dataset = master.dataset
    rng = random.Random(config.seed)
    alarms = _make_alarm_events(master, config, rng)
    dataset.tables["alarm_event"] = alarms
    dataset.replay_deliveries = _make_replay_deliveries(alarms, config)
    dataset.tables["machine_state_event"] = _make_machine_state_events(
        master, config, alarms
    )
    dataset.tables["acknowledgement"] = _make_acknowledgements(
        master, config, alarms, rng
    )
    _make_incidents_and_actions(master, config, dataset)
    for alarm in alarms:
        alarm.pop("_source_index")
        alarm.pop("_is_trigger")
        alarm.pop("_is_flood")
    dataset.tables["alarm_event"].sort(key=lambda row: (row["event_at"], row["id"]))
    dataset.tables["machine_state_event"].sort(
        key=lambda row: (row["event_at"], row["id"])
    )
    dataset.tables["acknowledgement"].sort(
        key=lambda row: (row["acknowledged_at"], row["id"])
    )
    dataset.replay_deliveries.sort(
        key=lambda row: (row["delivered_at"], row["delivery_id"])
    )
    return dataset


def _cluster_indices(event_count: int) -> tuple[set[int], set[int]]:
    available = event_count - 2
    nuisance_size = min(NUISANCE_CLUSTER_SIZE, max(3, event_count // 1000), available // 2)
    flood_size = min(ALARM_FLOOD_SIZE, max(3, event_count // 250), available - nuisance_size)
    nuisance_start = max(1, min(event_count // 4, event_count - 1 - nuisance_size - flood_size))
    flood_start = max(event_count // 2, nuisance_start + nuisance_size)
    if flood_start + flood_size >= event_count:
        flood_start = event_count - 1 - flood_size
    nuisance = set(range(nuisance_start, nuisance_start + nuisance_size))
    flood = set(range(flood_start, flood_start + flood_size))
    return nuisance, flood


def _make_alarm_events(
    master: MasterData,
    config: GenerationConfig,
    rng: random.Random,
) -> list[Record]:
    candidates = master.machines_with_alarms
    if not candidates:
        raise ValueError("at least one machine must have an alarm definition")

    nuisance_indices, flood_indices = _cluster_indices(config.alarm_event_count)
    interval = max(1, config.alarm_event_count // 50)
    trigger_indices = {
        index
        for index in range(config.alarm_event_count)
        if index % interval == 0 and index not in nuisance_indices | flood_indices
    }
    trigger_indices.add(0)
    nuisance_machine = candidates[-1]
    nuisance_definitions = master.alarm_definitions_by_machine_id[nuisance_machine["id"]]
    nuisance_definition = next(
        (row for row in nuisance_definitions if row["severity"] in {"low", "medium", "info"}),
        nuisance_definitions[-1],
    )
    flood_machine = candidates[0]
    flood_definitions = master.alarm_definitions_by_machine_id[flood_machine["id"]]
    flood_definition = next(
        (row for row in flood_definitions if row["severity"] in {"medium", "low"}),
        flood_definitions[-1],
    )

    duration_seconds = config.days * 24 * 60 * 60
    nuisance_start = config.start_at + timedelta(seconds=duration_seconds // 4)
    flood_start = config.start_at + timedelta(seconds=(duration_seconds * 3) // 4)
    planned: list[tuple[int, datetime, Record, Record, bool, bool]] = []
    trigger_number = 0

    for index in range(config.alarm_event_count):
        is_nuisance = index in nuisance_indices
        is_flood = index in flood_indices
        is_trigger = index in trigger_indices and not (is_nuisance or is_flood)
        if index == 0:
            is_trigger = True
        if is_flood:
            machine = flood_machine
            definition = flood_definition
        elif is_nuisance:
            machine = nuisance_machine
            definition = nuisance_definition
        elif is_trigger:
            machine = candidates[trigger_number % len(candidates)]
            trigger_number += 1
            definitions = master.alarm_definitions_by_machine_id[machine["id"]]
            definition = next(
                (row for row in definitions if row["severity"] == "critical"),
                definitions[0],
            )
        else:
            machine = rng.choice(candidates)
            definition = rng.choice(
                master.alarm_definitions_by_machine_id[machine["id"]]
            )

        if index == 0:
            event_at = config.start_at + timedelta(seconds=60)
        elif index == config.alarm_event_count - 1:
            event_at = config.end_at - timedelta(seconds=1)
        elif is_nuisance:
            event_at = nuisance_start + timedelta(seconds=index - min(nuisance_indices))
        elif is_flood:
            event_at = flood_start + timedelta(seconds=index - min(flood_indices))
        else:
            event_at = config.start_at + timedelta(
                seconds=60 + rng.randrange(max(1, duration_seconds - 120))
            )
        planned.append((index, event_at, machine, definition, is_trigger, is_flood))

    planned.sort(key=lambda item: (item[1], item[0]))
    events: list[Record] = []
    for sequence, (source_index, event_at, machine, definition, is_trigger, is_flood) in enumerate(
        planned, start=1
    ):
        external_event_id = f"EVT-{config.start_at:%Y%m%d}-{sequence:06d}"
        event_id = deterministic_uuid(config.seed, "alarm_event", external_event_id)
        received_at = event_at + timedelta(seconds=rng.randint(1, 30))
        event = {
            "id": event_id,
            "machine_id": machine["id"],
            "alarm_definition_id": definition["id"],
            "external_event_id": external_event_id,
            "event_at": event_at,
            "received_at": received_at,
            "status": "active",
            "cleared_at": None,
            "issue_type": None,
            "created_at": received_at,
            "_source_index": source_index,
            "_is_trigger": is_trigger,
            "_is_flood": is_flood,
        }
        events.append(event)
    return events


def _make_replay_deliveries(alarms: list[Record], config: GenerationConfig) -> list[Record]:
    deliveries: list[Record] = []
    for alarm in alarms:
        delivery_id = str(
            deterministic_uuid(config.seed, "delivery", alarm["external_event_id"])
        )
        delivery = {
            "delivery_id": delivery_id,
            "external_event_id": alarm["external_event_id"],
            "machine_id": alarm["machine_id"],
            "alarm_definition_id": alarm["alarm_definition_id"],
            "event_at": alarm["event_at"],
            "delivered_at": alarm["received_at"],
            "replay_of_delivery_id": None,
        }
        deliveries.append(delivery)
    return deliveries


def _make_machine_state_events(
    master: MasterData,
    config: GenerationConfig,
    alarms: list[Record],
) -> list[Record]:
    states: list[Record] = []
    duration_seconds = config.days * 24 * 60 * 60
    for machine_index, machine in enumerate(master.machines, start=1):
        interlock = master.interlocks_by_machine_id[machine["id"]][0]
        for slot, fraction, state in (
            (1, 0.1, "running"),
            (2, 0.5, "stopped"),
            (3, 0.9, "running"),
        ):
            event_at = config.start_at + timedelta(
                seconds=int(duration_seconds * fraction) + machine_index
            )
            source_event_id = f"STATE-{config.start_at:%Y%m%d}-{machine_index:04d}-{slot}"
            states.append(
                _machine_state_row(
                    config,
                    key=source_event_id,
                    machine_id=machine["id"],
                    interlock_id=interlock["id"],
                    state=state,
                    interlock_state=interlock["required_state"],
                    event_at=event_at,
                    source_event_id=source_event_id,
                )
            )

    trigger_number = 0
    for alarm in alarms:
        if not alarm["_is_trigger"]:
            continue
        trigger_number += 1
        event_at = alarm["event_at"] - timedelta(seconds=30)
        source_event_id = f"FAULT-{config.start_at:%Y%m%d}-{trigger_number:06d}"
        states.append(
            _machine_state_row(
                config,
                key=source_event_id,
                machine_id=alarm["machine_id"],
                interlock_id=None,
                state="fault",
                interlock_state=None,
                event_at=event_at,
                source_event_id=source_event_id,
            )
        )
    return states


def _machine_state_row(
    config: GenerationConfig,
    *,
    key: str,
    machine_id: Any,
    interlock_id: Any,
    state: str,
    interlock_state: str | None,
    event_at: datetime,
    source_event_id: str,
) -> Record:
    return {
        "id": deterministic_uuid(config.seed, "machine_state_event", key),
        "machine_id": machine_id,
        "interlock_id": interlock_id,
        "state": state,
        "interlock_state": interlock_state,
        "event_at": event_at,
        "source_event_id": source_event_id,
        "issue_type": None,
        "created_at": event_at,
    }


def _make_acknowledgements(
    master: MasterData,
    config: GenerationConfig,
    alarms: list[Record],
    rng: random.Random,
) -> list[Record]:
    acknowledgements: list[Record] = []
    for index, alarm in enumerate(alarms):
        definition = master.alarm_definition_by_id[alarm["alarm_definition_id"]]
        if not definition["acknowledgement_required"]:
            continue

        roll = rng.random()
        if index != 0 and roll < 0.02:
            continue
        sla_seconds = definition["ack_sla_seconds"]
        if roll >= 0.9:
            delay_seconds = sla_seconds + rng.randint(1, max(2, sla_seconds // 2))
            status = "late"
        else:
            delay_seconds = rng.randint(1, sla_seconds)
            status = "acknowledged"

        acknowledged_at = alarm["event_at"] + timedelta(seconds=delay_seconds)
        plant = master.plant_by_machine_id[alarm["machine_id"]]
        shift_number = acknowledged_at.hour // 8
        shift_key = (
            plant["id"],
            acknowledged_at.strftime("%Y-%m-%d"),
            shift_number,
        )
        shift = master.shifts_by_slot.get(shift_key)
        if shift is None:
            raise ValueError(f"no operator shift covers acknowledgement at {acknowledged_at}")
        business_id = acknowledgement_business_id(alarm["external_event_id"], 1)
        acknowledgement_id = deterministic_uuid(
            config.seed, "acknowledgement", business_id
        )
        acknowledgements.append(
            {
                "id": acknowledgement_id,
                "business_id": business_id,
                "alarm_event_id": alarm["id"],
                "operator_shift_id": shift["id"],
                "acknowledged_at": acknowledged_at,
                "status": status,
                "issue_type": None,
                "created_at": acknowledged_at,
            }
        )
    return acknowledgements


def _make_incidents_and_actions(
    master: MasterData,
    config: GenerationConfig,
    dataset: SyntheticDataset,
) -> None:
    state_events_by_machine: dict[Any, list[Record]] = {}
    for state_event in dataset.tables["machine_state_event"]:
        state_events_by_machine.setdefault(state_event["machine_id"], []).append(state_event)

    incident_counter = 0
    for alarm in dataset.tables["alarm_event"]:
        definition = master.alarm_definition_by_id[alarm["alarm_definition_id"]]
        if definition["severity"] not in {"high", "critical"}:
            continue
        has_unsafe_context = any(
            state_event["state"] in {"fault", "interlock_bypassed"}
            and timedelta(0) <= alarm["event_at"] - state_event["event_at"] <= INCIDENT_CONTEXT_WINDOW
            for state_event in state_events_by_machine.get(alarm["machine_id"], ())
        )
        if not has_unsafe_context:
            continue

        incident_counter += 1
        year = alarm["event_at"].year
        incident_code = incident_business_id(year, incident_counter)
        incident_id = deterministic_uuid(config.seed, "incident", incident_code)
        opened_at = alarm["event_at"] + timedelta(seconds=30)
        completion_delay = timedelta(hours=4 if definition["severity"] == "critical" else 8)
        completed_at = opened_at + completion_delay
        verified_at = completed_at + timedelta(hours=1)
        incident = {
            "id": incident_id,
            "business_id": incident_code,
            "machine_id": alarm["machine_id"],
            "opened_at": opened_at,
            "status": "closed",
            "issue_type": "unsafe_condition",
            "created_at": opened_at,
            "updated_at": verified_at + timedelta(seconds=1),
        }
        dataset.tables["incident"].append(incident)
        dataset.tables["incident_alarm_event"].append(
            {
                "id": deterministic_uuid(
                    config.seed,
                    "incident_alarm_event",
                    f"{incident_id}:{alarm['id']}",
                ),
                "incident_id": incident_id,
                "alarm_event_id": alarm["id"],
                "linked_at": opened_at,
            }
        )

        action_code = corrective_action_business_id(incident_code, 1)
        action = {
            "id": deterministic_uuid(config.seed, "corrective_action", action_code),
            "business_id": action_code,
            "incident_id": incident_id,
            "owner": f"MAINT-{master.plant_by_machine_id[alarm['machine_id']]['plant_code']}-01",
            "due_at": opened_at + timedelta(days=1 if definition["severity"] == "critical" else 2),
            "status": "closed",
            "completed_at": completed_at,
            "verification_result": "passed",
            "verification_evidence": f"VERIFIED-{incident_code}",
            "created_at": opened_at,
            "updated_at": verified_at + timedelta(seconds=1),
        }
        dataset.tables["corrective_action"].append(action)
