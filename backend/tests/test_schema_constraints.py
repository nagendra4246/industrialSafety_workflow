from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import Base


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def insert_plant(connection, *, code: str = "PLANT-A") -> uuid.UUID:
    plant_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO plant (id, plant_code, name, status, effective_from)
            VALUES (:id, :code, 'Plant A', 'active', :effective_from)
            """
        ),
        {"id": plant_id, "code": code, "effective_from": NOW},
    )
    return plant_id


def insert_machine(connection, plant_id: uuid.UUID, *, code: str = "M-01") -> uuid.UUID:
    machine_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO machine (id, plant_id, machine_code, name, status, effective_from)
            VALUES (:id, :plant_id, :code, 'Machine', 'active', :effective_from)
            """
        ),
        {"id": machine_id, "plant_id": plant_id, "code": code, "effective_from": NOW},
    )
    return machine_id


def insert_zone(connection, plant_id: uuid.UUID, *, code: str = "Z-01") -> uuid.UUID:
    zone_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO safety_zone
                (id, plant_id, business_id, zone_code, name, status, effective_from)
            VALUES (:id, :plant_id, :business_id, :code, 'Zone', 'active', :effective_from)
            """
        ),
        {
            "id": zone_id,
            "plant_id": plant_id,
            "business_id": f"SAFE-{plant_id}-001",
            "code": code,
            "effective_from": NOW,
        },
    )
    return zone_id


def test_schema_has_exactly_the_required_domain_tables(connection):
    expected = {
        "plant",
        "machine",
        "safety_zone",
        "interlock",
        "alarm_definition",
        "alarm_event",
        "operator_shift",
        "acknowledgement",
        "incident",
        "corrective_action",
        "machine_safety_zone",
        "incident_alarm_event",
        "machine_state_event",
        "lifecycle_history",
        "alembic_version",
    }
    actual = set(
        connection.execute(
            text(
                """
                SELECT tablename FROM pg_catalog.pg_tables
                WHERE schemaname = current_schema()
                """
            )
        ).scalars()
    )
    assert actual == expected


def test_primary_and_foreign_keys_are_enforced(connection):
    plant_id = insert_plant(connection)
    machine_id = insert_machine(connection, plant_id)

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    """
                    INSERT INTO machine (id, plant_id, machine_code, name, status, effective_from)
                    VALUES (:id, :plant_id, 'M-DUPLICATE-PK', 'Duplicate key', 'active', :effective_from)
                    """
                ),
                {"id": machine_id, "plant_id": plant_id, "effective_from": NOW},
            )

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_machine(connection, plant_id, code="M-01")

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_machine(connection, uuid.uuid4(), code="ORPHAN")

    assert connection.execute(
        text("SELECT id FROM machine WHERE id = :id"), {"id": machine_id}
    ).scalar_one() == machine_id


def test_short_codes_are_unique_only_within_their_scope(connection):
    first_plant = insert_plant(connection, code="PLANT-A")
    second_plant = insert_plant(connection, code="PLANT-B")
    insert_machine(connection, first_plant, code="M-01")
    insert_machine(connection, second_plant, code="M-01")

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_machine(connection, first_plant, code="M-01")

    insert_zone(connection, first_plant, code="Z-01")
    insert_zone(connection, second_plant, code="Z-01")
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_zone(connection, first_plant, code="Z-01")

    first_machine = connection.execute(
        text("SELECT id FROM machine WHERE plant_id = :plant_id ORDER BY machine_code LIMIT 1"),
        {"plant_id": first_plant},
    ).scalar_one()
    second_machine = connection.execute(
        text("SELECT id FROM machine WHERE plant_id = :plant_id ORDER BY machine_code LIMIT 1"),
        {"plant_id": second_plant},
    ).scalar_one()

    def add_alarm_definition(machine_id: uuid.UUID, definition_id: uuid.UUID) -> None:
        connection.execute(
            text(
                """
                INSERT INTO alarm_definition
                    (id, machine_id, business_id, alarm_code, name, severity,
                     acknowledgement_required, ack_sla_seconds, status, effective_from)
                VALUES (:id, :machine_id, :business_id, 'AL-01', 'Alarm',
                        'high', true, 300, 'active', :effective_from)
                """
            ),
            {
                "id": definition_id,
                "machine_id": machine_id,
                "business_id": f"ALM-{definition_id}",
                "effective_from": NOW,
            },
        )

    add_alarm_definition(first_machine, uuid.uuid4())
    add_alarm_definition(second_machine, uuid.uuid4())
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            add_alarm_definition(first_machine, uuid.uuid4())


def test_machine_safety_zone_bridge_enforces_same_plant_and_unique_link(connection):
    plant_id = insert_plant(connection)
    machine_id = insert_machine(connection, plant_id)
    zone_id = insert_zone(connection, plant_id)
    bridge_id = uuid.uuid4()
    values = {
        "id": bridge_id,
        "machine_id": machine_id,
        "zone_id": zone_id,
        "plant_id": plant_id,
        "effective_from": NOW,
    }
    sql = text(
        """
        INSERT INTO machine_safety_zone
            (id, machine_id, safety_zone_id, plant_id, effective_from)
        VALUES (:id, :machine_id, :zone_id, :plant_id, :effective_from)
        """
    )
    connection.execute(sql, values)

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(sql, {**values, "id": uuid.uuid4()})

    other_plant = insert_plant(connection, code="PLANT-B")
    other_zone = insert_zone(connection, other_plant, code="Z-01")
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                sql,
                {
                    **values,
                    "id": uuid.uuid4(),
                    "zone_id": other_zone,
                    "effective_from": NOW + timedelta(days=1),
                },
            )


def test_incident_alarm_bridge_is_many_to_many_with_no_duplicate_pair(connection):
    plant_id = insert_plant(connection)
    machine_id = insert_machine(connection, plant_id)
    alarm_definition_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO alarm_definition
                (id, machine_id, business_id, alarm_code, name, severity,
                 acknowledgement_required, ack_sla_seconds, status, effective_from)
            VALUES (:id, :machine_id, 'ALM-M-01-000001', 'AL-01', 'Alarm',
                    'high', true, 300, 'active', :effective_from)
            """
        ),
        {"id": alarm_definition_id, "machine_id": machine_id, "effective_from": NOW},
    )
    alarm_ids = [uuid.uuid4(), uuid.uuid4()]
    for index, alarm_id in enumerate(alarm_ids):
        connection.execute(
            text(
                """
                INSERT INTO alarm_event
                    (id, machine_id, alarm_definition_id, external_event_id, event_at, received_at)
                VALUES (:id, :machine_id, :definition_id, :external_id, :event_at, :received_at)
                """
            ),
            {
                "id": alarm_id,
                "machine_id": machine_id,
                "definition_id": alarm_definition_id,
                "external_id": f"EXT-{index}",
                "event_at": NOW + timedelta(minutes=index),
                "received_at": NOW + timedelta(minutes=index),
            },
        )
    incident_ids = [uuid.uuid4(), uuid.uuid4()]
    for index, incident_id in enumerate(incident_ids):
        connection.execute(
            text(
                """
                INSERT INTO incident (id, business_id, machine_id, opened_at, status, issue_type)
                VALUES (:id, :business_id, :machine_id, :opened_at, 'open', 'unsafe_condition')
                """
            ),
            {
                "id": incident_id,
                "business_id": f"INC-2026-{index + 1:06d}",
                "machine_id": machine_id,
                "opened_at": NOW,
            },
        )

    for incident_id in incident_ids:
        for alarm_id in alarm_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO incident_alarm_event (id, incident_id, alarm_event_id)
                    VALUES (:id, :incident_id, :alarm_id)
                    """
                ),
                {"id": uuid.uuid4(), "incident_id": incident_id, "alarm_id": alarm_id},
            )
    assert connection.execute(text("SELECT count(*) FROM incident_alarm_event")).scalar_one() == 4

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    """
                    INSERT INTO incident_alarm_event (id, incident_id, alarm_event_id)
                    VALUES (:id, :incident_id, :alarm_id)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "incident_id": incident_ids[0],
                    "alarm_id": alarm_ids[0],
                },
            )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("alarm_event", "id", None),
        ("machine_state_event", "id", None),
        ("lifecycle_history", "id", None),
    ],
)
def test_append_only_event_tables_reject_update_and_delete(connection, table, column, value):
    plant_id = insert_plant(connection)
    machine_id = insert_machine(connection, plant_id)
    event_id = uuid.uuid4()
    if table == "alarm_event":
        definition_id = uuid.uuid4()
        connection.execute(
            text(
                """
                INSERT INTO alarm_definition
                    (id, machine_id, business_id, alarm_code, name, severity,
                     acknowledgement_required, ack_sla_seconds, status, effective_from)
                VALUES (:id, :machine_id, 'ALM-M-01-000001', 'AL-01', 'Alarm',
                        'high', true, 300, 'active', :effective_from)
                """
            ),
            {"id": definition_id, "machine_id": machine_id, "effective_from": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO alarm_event
                    (id, machine_id, alarm_definition_id, external_event_id, event_at, received_at)
                VALUES (:id, :machine_id, :definition_id, 'EVENT-1', :event_at, :received_at)
                """
            ),
            {
                "id": event_id,
                "machine_id": machine_id,
                "definition_id": definition_id,
                "event_at": NOW,
                "received_at": NOW,
            },
        )
    elif table == "machine_state_event":
        connection.execute(
            text(
                """
                INSERT INTO machine_state_event (id, machine_id, state, event_at, source_event_id)
                VALUES (:id, :machine_id, 'running', :event_at, 'SOURCE-1')
                """
            ),
            {"id": event_id, "machine_id": machine_id, "event_at": NOW},
        )
    else:
        connection.execute(
            text(
                """
                INSERT INTO lifecycle_history
                    (id, entity_type, entity_id, transition, occurred_at, payload)
                VALUES (:id, 'machine', :entity_id, 'created', :occurred_at, '{}')
                """
            ),
            {"id": event_id, "entity_id": machine_id, "occurred_at": NOW},
        )

    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(f"UPDATE {table} SET id = id WHERE id = :id"), {"id": event_id})

    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": event_id})


def test_business_ids_cannot_be_changed(connection):
    plant_id = insert_plant(connection)
    zone_id = insert_zone(connection, plant_id)
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(
                text("UPDATE safety_zone SET business_id = 'CHANGED' WHERE id = :id"),
                {"id": zone_id},
            )


def test_acknowledgement_is_optional_per_alarm_but_requires_alarm_and_shift(connection):
    plant_id = insert_plant(connection)
    machine_id = insert_machine(connection, plant_id)
    definition_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO alarm_definition
                (id, machine_id, business_id, alarm_code, name, severity,
                 acknowledgement_required, ack_sla_seconds, status, effective_from)
            VALUES (:id, :machine_id, 'ALM-M-01-000001', 'AL-01', 'Alarm',
                    'high', true, 300, 'active', :effective_from)
            """
        ),
        {"id": definition_id, "machine_id": machine_id, "effective_from": NOW},
    )
    event_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO alarm_event
                (id, machine_id, alarm_definition_id, external_event_id, event_at, received_at)
            VALUES (:id, :machine_id, :definition_id, 'EVENT-OPTIONAL', :event_at, :received_at)
            """
        ),
        {
            "id": event_id,
            "machine_id": machine_id,
            "definition_id": definition_id,
            "event_at": NOW,
            "received_at": NOW,
        },
    )
    assert connection.execute(text("SELECT count(*) FROM acknowledgement")).scalar_one() == 0

    shift_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO operator_shift (id, shift_code, operator_code, starts_at, ends_at)
            VALUES (:id, 'SHIFT-1', 'OP-1', :starts_at, :ends_at)
            """
        ),
        {
            "id": shift_id,
            "starts_at": NOW - timedelta(days=1),
            "ends_at": NOW + timedelta(days=1),
        },
    )
    acknowledgement_sql = text(
        """
        INSERT INTO acknowledgement
            (id, business_id, alarm_event_id, operator_shift_id, acknowledged_at, status, issue_type)
        VALUES (:id, :business_id, :alarm_id, :shift_id, :acknowledged_at, 'acknowledged', :issue_type)
        """
    )

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                acknowledgement_sql,
                {
                    "id": uuid.uuid4(),
                    "business_id": "ACK-EVENT-01",
                    "alarm_id": event_id,
                    "shift_id": uuid.uuid4(),
                    "acknowledged_at": NOW,
                    "issue_type": None,
                },
            )

    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                acknowledgement_sql,
                {
                    "id": uuid.uuid4(),
                    "business_id": "ACK-EVENT-02",
                    "alarm_id": uuid.uuid4(),
                    "shift_id": shift_id,
                    "acknowledged_at": NOW,
                    "issue_type": None,
                },
            )

    connection.execute(
        acknowledgement_sql,
        {
            "id": uuid.uuid4(),
            "business_id": "ACK-EVENT-03",
            "alarm_id": event_id,
            "shift_id": shift_id,
            "acknowledged_at": NOW - timedelta(minutes=1),
            "issue_type": "ack_before_alarm",
        },
    )
    assert connection.execute(text("SELECT count(*) FROM acknowledgement")).scalar_one() == 1


def test_schema_metadata_matches_migrated_relations(connection):
    assert set(Base.metadata.tables) == {
        "plant",
        "machine",
        "safety_zone",
        "machine_safety_zone",
        "interlock",
        "alarm_definition",
        "alarm_event",
        "operator_shift",
        "acknowledgement",
        "incident",
        "incident_alarm_event",
        "corrective_action",
        "machine_state_event",
        "lifecycle_history",
    }
