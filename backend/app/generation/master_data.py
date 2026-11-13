from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .dataset import GenerationConfig, Record, SyntheticDataset
from .ids import (
    alarm_definition_business_id,
    deterministic_uuid,
    safety_zone_business_id,
)


@dataclass
class MasterData:
    dataset: SyntheticDataset
    plants: list[Record]
    machines: list[Record]
    machine_by_id: dict[Any, Record]
    plant_by_machine_id: dict[Any, Record]
    zones_by_plant_id: dict[Any, list[Record]]
    interlocks_by_machine_id: dict[Any, list[Record]]
    alarm_definitions_by_machine_id: dict[Any, list[Record]]
    alarm_definition_by_id: dict[Any, Record]
    shifts_by_slot: dict[tuple[Any, str, int], Record]

    @property
    def machines_with_alarms(self) -> list[Record]:
        return [
            machine
            for machine in self.machines
            if self.alarm_definitions_by_machine_id.get(machine["id"])
        ]


SEVERITIES = ("critical", "high", "medium", "low", "info")
ACK_SLA_SECONDS = {
    "critical": 60,
    "high": 300,
    "medium": 900,
    "low": 1800,
    "info": 3600,
}
INTERLOCK_STATES = ("closed", "deenergized")


def build_master_data(config: GenerationConfig) -> MasterData:
    dataset = SyntheticDataset(config)
    start = config.start_at

    plants: list[Record] = []
    for index in range(config.plant_count):
        plant_code = f"PLT-{index + 1:03d}"
        plant_id = deterministic_uuid(config.seed, "plant", plant_code)
        plant = {
            "id": plant_id,
            "plant_code": plant_code,
            "name": f"Plant {index + 1:02d}",
            "status": "active",
            "effective_from": start,
            "effective_to": None,
            "created_at": start,
            "updated_at": start,
        }
        plants.append(plant)
    dataset.tables["plant"] = plants

    machines: list[Record] = []
    machine_by_id: dict[Any, Record] = {}
    plant_by_machine_id: dict[Any, Record] = {}
    for index in range(config.machine_count):
        plant = plants[index % len(plants)]
        local_number = index // len(plants) + 1
        machine_code = f"MCH-{plant['plant_code']}-{local_number:03d}"
        machine = {
            "id": deterministic_uuid(config.seed, "machine", machine_code),
            "plant_id": plant["id"],
            "machine_code": machine_code,
            "name": f"Machine {index + 1:03d}",
            "status": "active",
            "effective_from": start,
            "effective_to": None,
            "created_at": start,
            "updated_at": start,
        }
        machines.append(machine)
        machine_by_id[machine["id"]] = machine
        plant_by_machine_id[machine["id"]] = plant
    dataset.tables["machine"] = machines

    zones_by_plant_id: dict[Any, list[Record]] = {}
    for plant in plants:
        zones: list[Record] = []
        for counter in range(1, 4):
            zone_code = f"SZ-{counter:03d}"
            zone = {
                "id": deterministic_uuid(
                    config.seed, "safety_zone", f"{plant['plant_code']}:{zone_code}"
                ),
                "plant_id": plant["id"],
                "business_id": safety_zone_business_id(plant["plant_code"], counter),
                "zone_code": zone_code,
                "name": f"{plant['plant_code']} Safety Zone {counter}",
                "status": "active",
                "effective_from": start,
                "effective_to": None,
                "created_at": start,
                "updated_at": start,
            }
            zones.append(zone)
        zones_by_plant_id[plant["id"]] = zones
        dataset.tables["safety_zone"].extend(zones)

    for machine in machines:
        zones = zones_by_plant_id[machine["plant_id"]]
        for zone_index in sorted({0, (len(zones) - 1) if machine["id"].int % 2 else 1}):
            zone = zones[zone_index]
            link_id = deterministic_uuid(
                config.seed,
                "machine_safety_zone",
                f"{machine['id']}:{zone['id']}:0",
            )
            dataset.tables["machine_safety_zone"].append(
                {
                    "id": link_id,
                    "machine_id": machine["id"],
                    "safety_zone_id": zone["id"],
                    "plant_id": machine["plant_id"],
                    "effective_from": start,
                    "effective_to": None,
                }
            )

    interlocks_by_machine_id: dict[Any, list[Record]] = {}
    for machine in machines:
        interlocks: list[Record] = []
        for counter, required_state in enumerate(INTERLOCK_STATES, start=1):
            interlock_code = f"IL-{counter:03d}"
            interlock = {
                "id": deterministic_uuid(
                    config.seed, "interlock", f"{machine['machine_code']}:{interlock_code}"
                ),
                "machine_id": machine["id"],
                "interlock_code": interlock_code,
                "description": f"Machine safety interlock {counter}",
                "required_state": required_state,
                "status": "active",
                "effective_from": start,
                "effective_to": None,
                "created_at": start,
                "updated_at": start,
            }
            interlocks.append(interlock)
        interlocks_by_machine_id[machine["id"]] = interlocks
        dataset.tables["interlock"].extend(interlocks)

    alarm_definitions_by_machine_id: dict[Any, list[Record]] = {
        machine["id"]: [] for machine in machines
    }
    alarm_definition_by_id: dict[Any, Record] = {}
    alarm_names = {
        "critical": "Emergency stop circuit",
        "high": "Guard interlock violation",
        "medium": "Pressure threshold",
        "low": "Temperature warning",
        "info": "Operating state notice",
    }
    local_counters = {machine["id"]: 0 for machine in machines}
    for index in range(config.alarm_definition_count):
        machine = machines[index % len(machines)]
        local_counters[machine["id"]] += 1
        counter = local_counters[machine["id"]]
        severity = SEVERITIES[(counter - 1) % len(SEVERITIES)]
        alarm_code = f"AL-{counter:03d}"
        definition = {
            "id": deterministic_uuid(
                config.seed, "alarm_definition", f"{machine['machine_code']}:{counter}"
            ),
            "machine_id": machine["id"],
            "business_id": alarm_definition_business_id(machine["machine_code"], counter),
            "alarm_code": alarm_code,
            "name": f"{alarm_names[severity]} {counter:03d}",
            "severity": severity,
            "acknowledgement_required": severity != "info",
            "ack_sla_seconds": ACK_SLA_SECONDS[severity],
            "status": "active",
            "effective_from": start,
            "effective_to": None,
            "created_at": start,
            "updated_at": start,
        }
        alarm_definitions_by_machine_id[machine["id"]].append(definition)
        alarm_definition_by_id[definition["id"]] = definition
        dataset.tables["alarm_definition"].append(definition)

    shifts_by_slot: dict[tuple[Any, str, int], Record] = {}
    for plant in plants:
        for day_offset in range(config.days + 1):
            day_start = start + timedelta(days=day_offset)
            for shift_number in range(3):
                shift_start = day_start + timedelta(hours=8 * shift_number)
                shift_code = (
                    f"SHIFT-{plant['plant_code']}-{shift_start:%Y%m%d}-{shift_number + 1}"
                )
                shift = {
                    "id": deterministic_uuid(config.seed, "operator_shift", shift_code),
                    "shift_code": shift_code,
                    "operator_code": f"OP-{plant['plant_code']}-{shift_number + 1:02d}",
                    "starts_at": shift_start,
                    "ends_at": shift_start + timedelta(hours=8),
                    "created_at": start,
                }
                shifts_by_slot[(plant["id"], shift_start.strftime("%Y-%m-%d"), shift_number)] = shift
                dataset.tables["operator_shift"].append(shift)

    return MasterData(
        dataset=dataset,
        plants=plants,
        machines=machines,
        machine_by_id=machine_by_id,
        plant_by_machine_id=plant_by_machine_id,
        zones_by_plant_id=zones_by_plant_id,
        interlocks_by_machine_id=interlocks_by_machine_id,
        alarm_definitions_by_machine_id=alarm_definitions_by_machine_id,
        alarm_definition_by_id=alarm_definition_by_id,
        shifts_by_slot=shifts_by_slot,
    )
