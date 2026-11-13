from __future__ import annotations

import uuid


def deterministic_uuid(seed: int, entity: str, key: str) -> uuid.UUID:
    name = f"industrial-safety-workflow:{seed}:{entity}:{key}"
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def safety_zone_business_id(plant_id: str, counter: int) -> str:
    return f"SAFE-{plant_id}-{counter:03d}"


def alarm_definition_business_id(machine_id: str, counter: int) -> str:
    return f"ALM-{machine_id}-{counter:06d}"


def incident_business_id(year: int, counter: int) -> str:
    return f"INC-{year}-{counter:06d}"


def acknowledgement_business_id(alarm_id: str, counter: int) -> str:
    return f"ACK-{alarm_id}-{counter:02d}"


def corrective_action_business_id(incident_id: str, counter: int) -> str:
    return f"ACT-{incident_id}-{counter:02d}"
