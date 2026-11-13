from datetime import datetime, timedelta, timezone
import uuid
from collections import Counter

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement,
    AcknowledgementStatus,
    AlarmDefinition,
    AlarmDefinitionStatus,
    AlarmSeverity,
    Interlock,
    InterlockState,
    InterlockStatus,
    Machine,
    MachineState,
    MachineStatus,
    OperatorShift,
    Plant,
    PlantStatus,
)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def create_fixture_data(connection):
    plant_id = uuid.uuid4()
    machine_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    shift_id = uuid.uuid4()
    with Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        session.add(
            Plant(
                id=plant_id,
                plant_code=f"PLANT-{uuid.uuid4().hex[:8]}",
                name="Test Plant",
                status=PlantStatus.ACTIVE,
                effective_from=NOW - timedelta(days=1),
            )
        )
        session.flush()
        session.add(
            Machine(
                id=machine_id,
                plant_id=plant_id,
                machine_code="M-01",
                name="Test Machine",
                status=MachineStatus.ACTIVE,
                effective_from=NOW - timedelta(days=1),
            )
        )
        session.flush()
        session.add_all(
            [
                AlarmDefinition(
                    id=definition_id,
                    machine_id=machine_id,
                    business_id=f"ALM-{machine_id}-000001",
                    alarm_code="A-01",
                    name="High pressure",
                    severity=AlarmSeverity.HIGH,
                    acknowledgement_required=True,
                    ack_sla_seconds=300,
                    status=AlarmDefinitionStatus.ACTIVE,
                    effective_from=NOW - timedelta(days=1),
                ),
                OperatorShift(
                    id=shift_id,
                    shift_code=f"SHIFT-{uuid.uuid4().hex[:8]}",
                    operator_code="OP-01",
                    starts_at=NOW - timedelta(hours=1),
                    ends_at=NOW + timedelta(hours=8),
                ),
            ]
        )
        session.commit()
    return {
        "plant_id": plant_id,
        "machine_id": machine_id,
        "alarm_definition_id": definition_id,
        "shift_id": shift_id,
    }


def state_payload(machine_id, event_at, source_event_id="STATE-1"):
    return {
        "machine_id": str(machine_id),
        "state": MachineState.FAULT.value,
        "event_at": event_at.isoformat(),
        "source_event_id": source_event_id,
    }


def alarm_payload(machine_id, definition_id, event_at, external_event_id="SOURCE-1"):
    return {
        "machine_id": str(machine_id),
        "alarm_definition_id": str(definition_id),
        "external_event_id": external_event_id,
        "event_at": event_at.isoformat(),
        "received_at": (event_at + timedelta(seconds=1)).isoformat(),
    }


def test_alarm_workflow_idempotency_acknowledgement_incident_and_verification(
    api_client, connection
):
    ids = create_fixture_data(connection)
    state_at = NOW
    alarm_at = NOW + timedelta(minutes=1)
    state_response = api_client.post(
        "/api/v1/machine-state-events",
        json=state_payload(ids["machine_id"], state_at),
    )
    assert state_response.status_code == 200, state_response.text

    alarm_body = alarm_payload(
        ids["machine_id"], ids["alarm_definition_id"], alarm_at
    )
    alarm_response = api_client.post("/api/v1/alarm-events", json=alarm_body)
    assert alarm_response.status_code == 200, alarm_response.text
    alarm_result = alarm_response.json()
    assert alarm_result["decision"]["incident_created"] is True
    assert alarm_result["decision"]["unsafe_context"] is True

    replay_response = api_client.post("/api/v1/alarm-events", json=alarm_body)
    assert replay_response.status_code == 200, replay_response.text
    assert replay_response.json()["idempotent"] is True

    alarm_id = alarm_result["event"]["id"]
    incident_id = alarm_result["decision"]["incident_id"]
    incident = api_client.get(f"/api/v1/incidents?machine_id={ids['machine_id']}")
    assert incident.status_code == 200
    assert incident.json()["total"] == 1
    evidence = api_client.get(f"/api/v1/incidents/{incident_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert len(evidence.json()["alarm_events"]) == 1
    assert len(evidence.json()["machine_state_events"]) == 1
    assert evidence.json()["traceability"]["traceable"] is True, evidence.json()[
        "traceability"
    ]
    assert evidence.json()["traceability"]["exceptions"] == []

    acknowledgement = api_client.post(
        "/api/v1/acknowledgements",
        json={
            "business_id": "ACK-SOURCE-1-01",
            "alarm_event_id": alarm_id,
            "operator_shift_id": str(ids["shift_id"]),
            "acknowledged_at": (alarm_at + timedelta(minutes=2)).isoformat(),
        },
    )
    assert acknowledgement.status_code == 200, acknowledgement.text
    assert acknowledgement.json()["acknowledgement"]["status"] == "acknowledged"

    incident_business_id = incident.json()["items"][0]["business_id"]
    action_response = api_client.post(
        "/api/v1/corrective-actions",
        json={
            "business_id": f"ACT-{incident_business_id}-01",
            "incident_id": incident_id,
            "owner": "maintenance-team",
            "created_at": (alarm_at + timedelta(minutes=3)).isoformat(),
            "due_at": (alarm_at + timedelta(days=1)).isoformat(),
        },
    )
    assert action_response.status_code == 200, action_response.text
    action_id = action_response.json()["action"]["id"]
    action_search = api_client.get(
        "/api/v1/corrective-actions?search=maintenance-team"
    )
    assert action_search.status_code == 200
    assert action_search.json()["total"] == 1

    premature_close = api_client.patch(
        f"/api/v1/corrective-actions/{action_id}/status",
        json={
            "status": "closed",
            "occurred_at": (alarm_at + timedelta(minutes=4)).isoformat(),
        },
    )
    assert premature_close.status_code == 409

    for status, minutes in (("in_progress", 5), ("completed", 6)):
        transition = api_client.patch(
            f"/api/v1/corrective-actions/{action_id}/status",
            json={
                "status": status,
                "occurred_at": (alarm_at + timedelta(minutes=minutes)).isoformat(),
            },
        )
        assert transition.status_code == 200, transition.text

    verification = api_client.post(
        f"/api/v1/corrective-actions/{action_id}/verify",
        json={
            "result": "passed",
            "evidence": "Guard and interlock function tested.",
            "verified_at": (alarm_at + timedelta(minutes=7)).isoformat(),
        },
    )
    assert verification.status_code == 200, verification.text
    assert verification.json()["action"]["status"] == "closed"

    for status, minutes in (("investigating", 8), ("resolved", 9), ("closed", 10)):
        transition = api_client.patch(
            f"/api/v1/incidents/{incident_id}/status",
            json={
                "status": status,
                "occurred_at": (alarm_at + timedelta(minutes=minutes)).isoformat(),
            },
        )
        assert transition.status_code == 200, transition.text

    history = api_client.get(
        f"/api/v1/lifecycle-history?entity_type=incident&entity_id={incident_id}"
    )
    assert history.status_code == 200
    assert [
        row["transition"]
        for row in history.json()["items"]
    ].count("opened") == 1
    summary = api_client.get("/api/v1/analytics/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["verified_corrective_action_count"] == 1
    assert summary.json()["closed_unverified_corrective_action_count"] == 0
    assert summary.json()["incident_traceability"] == {
        "incident_count": 1,
        "traceable_incident_count": 1,
        "traceability_exception_incident_count": 0,
        "missing_alarm_context_incident_count": 0,
        "missing_machine_state_context_incident_count": 0,
        "missing_lifecycle_history_incident_count": 0,
        "missing_action_lifecycle_history_incident_count": 0,
        "closed_unverified_action_incident_count": 0,
    }
    final_evidence = api_client.get(
        f"/api/v1/incidents/{incident_id}/evidence"
    )
    assert final_evidence.status_code == 200, final_evidence.text
    assert final_evidence.json()["traceability"]["traceable"] is True
    assert final_evidence.json()["traceability"]["action_lifecycle_record_count"] >= 1
    assert any(
        history["entity_type"] == "corrective_action"
        for history in final_evidence.json()["lifecycle_history"]
    )


def test_event_chronology_replay_conflict_and_bounded_pagination(
    api_client, connection
):
    ids = create_fixture_data(connection)
    first_at = NOW
    first = api_client.post(
        "/api/v1/alarm-events",
        json=alarm_payload(
            ids["machine_id"], ids["alarm_definition_id"], first_at, "PAGE-1"
        ),
    )
    assert first.status_code == 200, first.text

    replay = api_client.post(
        "/api/v1/alarm-events",
        json=alarm_payload(
            ids["machine_id"], ids["alarm_definition_id"], first_at, "PAGE-1"
        ),
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True

    conflict = api_client.post(
        "/api/v1/alarm-events",
        json=alarm_payload(
            ids["machine_id"],
            ids["alarm_definition_id"],
            first_at + timedelta(seconds=1),
            "PAGE-1",
        ),
    )
    assert conflict.status_code == 409

    for number in (2, 3):
        response = api_client.post(
            "/api/v1/alarm-events",
            json=alarm_payload(
                ids["machine_id"],
                ids["alarm_definition_id"],
                first_at + timedelta(minutes=number),
                f"PAGE-{number}",
            ),
        )
        assert response.status_code == 200, response.text

    first_page = api_client.get("/api/v1/alarm-events?limit=2&offset=0")
    second_page = api_client.get("/api/v1/alarm-events?limit=2&offset=2")
    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2
    assert len(second_page.json()["items"]) == 1
    assert first_page.json()["items"][0]["event_at"] < first_page.json()["items"][1]["event_at"]
    assert api_client.get("/api/v1/alarm-events?limit=201").status_code == 422

    state_response = api_client.post(
        "/api/v1/machine-state-events",
        json=state_payload(
            ids["machine_id"],
            first_at + timedelta(minutes=4),
            source_event_id="STATE-AFTER",
        ),
    )
    assert state_response.status_code == 200
    out_of_order_alarm = api_client.post(
        "/api/v1/alarm-events",
        json=alarm_payload(
            ids["machine_id"],
            ids["alarm_definition_id"],
            first_at + timedelta(minutes=3),
            "OUT-OF-ORDER",
        ),
    )
    assert out_of_order_alarm.status_code == 422


def test_openapi_and_analytics_summary_are_available(api_client, connection):
    ids = create_fixture_data(connection)
    for number in range(3):
        response = api_client.post(
            "/api/v1/alarm-events",
            json=alarm_payload(
                ids["machine_id"],
                ids["alarm_definition_id"],
                NOW + timedelta(seconds=number * 20),
                f"ANALYTICS-{number + 1}",
            ),
        )
        assert response.status_code == 200, response.text

    summary = api_client.get("/api/v1/analytics/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["alarm_event_count"] == 3
    assert summary.json()["alarm_events_by_severity"] == {"high": 3}
    assert summary.json()["repeated_alarm_cluster_count"] == 1
    clusters = api_client.get("/api/v1/analytics/alarm-clusters?kind=severe")
    assert clusters.status_code == 200, clusters.text
    assert clusters.json()["total"] == 1
    assert clusters.json()["items"][0]["event_count"] == 3
    assert clusters.json()["items"][0]["severity"] == "high"
    assert api_client.get(
        "/api/v1/analytics/alarm-clusters?kind=nuisance"
    ).json()["total"] == 0
    assert api_client.get("/api/v1/alarm-events?search=High%20pressure").json()[
        "total"
    ] == 3

    openapi = api_client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/api/v1/alarm-events" in openapi.json()["paths"]


def test_manual_incident_context_queries_are_batched(api_client, connection):
    ids = create_fixture_data(connection)
    state_response = api_client.post(
        "/api/v1/machine-state-events",
        json=state_payload(ids["machine_id"], NOW),
    )
    assert state_response.status_code == 200, state_response.text

    alarm_ids = []
    for number in range(3):
        response = api_client.post(
            "/api/v1/alarm-events",
            json=alarm_payload(
                ids["machine_id"],
                ids["alarm_definition_id"],
                NOW + timedelta(minutes=number + 1),
                f"BATCH-{number + 1}",
            ),
        )
        assert response.status_code == 200, response.text
        alarm_ids.append(response.json()["event"]["id"])

    statements = []

    def collect_selects(
        connection, cursor, statement, parameters, context, executemany
    ):
        normalized = statement.lower()
        if normalized.lstrip().startswith("select"):
            statements.append(normalized)

    event.listen(connection, "before_cursor_execute", collect_selects)
    try:
        response = api_client.post(
            "/api/v1/incidents",
            json={
                "business_id": "INC-2026-000100",
                "machine_id": str(ids["machine_id"]),
                "alarm_event_ids": alarm_ids,
                "opened_at": (NOW + timedelta(minutes=4)).isoformat(),
            },
        )
    finally:
        event.remove(connection, "before_cursor_execute", collect_selects)

    assert response.status_code == 200, response.text
    assert len(response.json()["linked_alarm_event_ids"]) == 3
    assert sum("from alarm_event" in statement for statement in statements) == 1
    assert sum(
        "from alarm_definition" in statement
        or "join alarm_definition" in statement
        for statement in statements
    ) == 1
    assert sum("from machine_state_event" in statement for statement in statements) == 1


def test_master_and_history_read_endpoints(api_client, connection):
    ids = create_fixture_data(connection)
    state_response = api_client.post(
        "/api/v1/machine-state-events",
        json=state_payload(ids["machine_id"], NOW),
    )
    assert state_response.status_code == 200, state_response.text

    paths = (
        "/api/v1/plants",
        "/api/v1/machines",
        "/api/v1/safety-zones",
        "/api/v1/interlocks",
        "/api/v1/alarm-definitions",
        "/api/v1/operator-shifts",
        f"/api/v1/machines/{ids['machine_id']}/state-events",
        "/api/v1/alarm-events",
        "/api/v1/acknowledgements",
        "/api/v1/incidents",
        "/api/v1/corrective-actions",
        "/api/v1/lifecycle-history",
        f"/api/v1/machines/{ids['machine_id']}/safety-zones",
    )
    for path in paths:
        response = api_client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        assert {"items", "total", "limit", "offset"} <= response.json().keys()
    machine_search = api_client.get("/api/v1/machines?search=Test%20Machine")
    assert machine_search.status_code == 200
    assert machine_search.json()["total"] == 1


def test_state_ingestion_rejects_inactive_interlock(api_client, connection):
    ids = create_fixture_data(connection)
    interlock_id = uuid.uuid4()
    with Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        session.add(
            Interlock(
                id=interlock_id,
                machine_id=ids["machine_id"],
                interlock_code="IL-01",
                description="Guard door interlock",
                required_state=InterlockState.CLOSED,
                status=InterlockStatus.INACTIVE,
                effective_from=NOW - timedelta(days=1),
            )
        )
        session.commit()

    payload = state_payload(ids["machine_id"], NOW)
    payload["interlock_id"] = str(interlock_id)
    payload["interlock_state"] = InterlockState.CLOSED.value
    response = api_client.post("/api/v1/machine-state-events", json=payload)
    assert response.status_code == 422


def test_analytics_reconciles_exact_sla_boundary_and_exceptions(
    api_client, connection
):
    ids = create_fixture_data(connection)
    events = []
    for number in range(4):
        event_at = NOW + timedelta(minutes=number * 2)
        response = api_client.post(
            "/api/v1/alarm-events",
            json=alarm_payload(
                ids["machine_id"],
                ids["alarm_definition_id"],
                event_at,
                f"METRIC-{number + 1}",
            ),
        )
        assert response.status_code == 200, response.text
        events.append(response.json()["event"])

    for number, response_seconds in ((0, 300), (1, 301)):
        acknowledged_at = datetime.fromisoformat(events[number]["event_at"]) + timedelta(
            seconds=response_seconds
        )
        acknowledgement = api_client.post(
            "/api/v1/acknowledgements",
            json={
                "business_id": f"ACK-METRIC-{number + 1}-01",
                "alarm_event_id": events[number]["id"],
                "operator_shift_id": str(ids["shift_id"]),
                "acknowledged_at": acknowledged_at.isoformat(),
            },
        )
        assert acknowledgement.status_code == 200, acknowledgement.text
        assert acknowledgement.json()["acknowledgement"]["status"] == (
            "acknowledged" if response_seconds == 300 else "late"
        )

    early_event_at = datetime.fromisoformat(events[3]["event_at"])
    connection.execute(
        Acknowledgement.__table__.insert(),
        {
            "id": uuid.uuid4(),
            "business_id": "ACK-METRIC-4-01",
            "alarm_event_id": uuid.UUID(events[3]["id"]),
            "operator_shift_id": ids["shift_id"],
            "acknowledged_at": early_event_at - timedelta(seconds=1),
            "status": AcknowledgementStatus.ACKNOWLEDGED,
            "created_at": early_event_at - timedelta(seconds=1),
        },
    )

    response = api_client.get("/api/v1/analytics/summary")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["alarm_event_count"] == 4
    assert summary["severe_alarm_event_count"] == 4
    assert summary["nuisance_alarm_event_count"] == 0
    assert summary["acknowledgement_count"] == 3
    assert summary["timely_acknowledgement_count"] == 1
    assert summary["late_acknowledgement_count"] == 1
    assert summary["acknowledgement_before_alarm_count"] == 1
    assert summary["unacknowledged_required_alarm_event_count"] == 1
    assert summary["acknowledgement_sla_exception_count"] == 3
    assert summary["acknowledgement_response_observation_count"] == 2
    assert summary["mean_acknowledgement_response_seconds"] == pytest.approx(300.5)
    assert summary["repeated_alarm_cluster_count"] == 0
    assert summary["alarm_cluster_count_by_kind"] == {
        "nuisance": 0,
        "severe": 0,
    }


def test_analytics_reconciles_generated_source_records_and_evaluates_labels(
    api_client, connection
):
    from app.generation.clusters import find_alarm_clusters
    from app.generation.dataset import GenerationConfig, TABLE_ORDER
    from app.generation.generator import generate_dataset
    from app.models import Base

    dataset = generate_dataset(
        GenerationConfig(
            seed=20261022,
            plant_count=2,
            machine_count=6,
            alarm_definition_count=18,
            alarm_event_count=400,
            days=3,
        )
    )
    for table_name in TABLE_ORDER:
        rows = dataset.tables[table_name]
        if rows:
            connection.execute(Base.metadata.tables[table_name].insert(), rows)

    definitions = {
        row["id"]: row for row in dataset.tables["alarm_definition"]
    }
    severity_counts = Counter(
        definitions[event["alarm_definition_id"]]["severity"]
        for event in dataset.tables["alarm_event"]
    )
    acknowledgements_by_alarm = {
        row["alarm_event_id"] for row in dataset.tables["acknowledgement"]
    }
    timely_count = late_count = before_count = excused_count = 0
    response_seconds = []
    for acknowledgement in dataset.tables["acknowledgement"]:
        alarm = next(
            event
            for event in dataset.tables["alarm_event"]
            if event["id"] == acknowledgement["alarm_event_id"]
        )
        definition = definitions[alarm["alarm_definition_id"]]
        elapsed = (
            acknowledgement["acknowledged_at"] - alarm["event_at"]
        ).total_seconds()
        if acknowledgement["status"] == "excused":
            excused_count += 1
        if elapsed < 0:
            before_count += 1
        else:
            response_seconds.append(elapsed)
            if elapsed > definition["ack_sla_seconds"]:
                late_count += 1
            else:
                timely_count += 1
    required_alarm_ids = {
        event["id"]
        for event in dataset.tables["alarm_event"]
        if definitions[event["alarm_definition_id"]]["acknowledgement_required"]
    }
    unacknowledged_required_count = len(
        required_alarm_ids - acknowledgements_by_alarm
    )
    clusters = find_alarm_clusters(dataset.tables["alarm_event"])
    cluster_counts = Counter(
        "nuisance"
        if definitions[cluster[0]["alarm_definition_id"]]["severity"]
        in {"info", "low", "medium"}
        else "severe"
        for cluster in clusters
    )
    actions = dataset.tables["corrective_action"]
    action_status_counts = Counter(row["status"] for row in actions)
    links_by_incident: dict[object, list[object]] = {}
    for link in dataset.tables["incident_alarm_event"]:
        links_by_incident.setdefault(link["incident_id"], []).append(
            link["alarm_event_id"]
        )
    state_ids = {
        str(row["id"]) for row in dataset.tables["machine_state_event"]
    }
    histories_by_incident: dict[object, list[dict]] = {}
    action_history_ids = set()
    for history in dataset.tables["lifecycle_history"]:
        if history["entity_type"] == "incident":
            histories_by_incident.setdefault(history["entity_id"], []).append(
                history
            )
        elif history["entity_type"] == "corrective_action":
            action_history_ids.add(history["entity_id"])
    actions_by_incident: dict[object, list[dict]] = {}
    for action in actions:
        actions_by_incident.setdefault(action["incident_id"], []).append(action)
    incident_traceability = {
        "incident_count": len(dataset.tables["incident"]),
        "traceable_incident_count": 0,
        "traceability_exception_incident_count": 0,
        "missing_alarm_context_incident_count": 0,
        "missing_machine_state_context_incident_count": 0,
        "missing_lifecycle_history_incident_count": 0,
        "missing_action_lifecycle_history_incident_count": 0,
        "closed_unverified_action_incident_count": 0,
    }
    for incident in dataset.tables["incident"]:
        incident_id = incident["id"]
        histories = histories_by_incident.get(incident_id, [])
        linked_alarm_ids = set(links_by_incident.get(incident_id, []))
        linked_state_ids = {
            str(context["machine_state_event_id"])
            for history in histories
            for context in history["payload"].get("linked_contexts", [])
            if context.get("machine_state_event_id")
        }
        incident_actions = actions_by_incident.get(incident_id, [])
        exceptions = {
            "missing_alarm_context_incident_count": not linked_alarm_ids,
            "missing_machine_state_context_incident_count": not (
                linked_state_ids & state_ids
            ),
            "missing_lifecycle_history_incident_count": not histories,
            "missing_action_lifecycle_history_incident_count": any(
                action["id"] not in action_history_ids
                for action in incident_actions
            ),
            "closed_unverified_action_incident_count": any(
                action["status"] == "closed"
                and action["verification_result"] != "passed"
                for action in incident_actions
            ),
        }
        for metric, has_exception in exceptions.items():
            incident_traceability[metric] += int(has_exception)
        if any(exceptions.values()):
            incident_traceability["traceability_exception_incident_count"] += 1
        else:
            incident_traceability["traceable_incident_count"] += 1

    summary_response = api_client.get("/api/v1/analytics/summary")
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert summary["alarm_event_count"] == len(dataset.tables["alarm_event"])
    assert summary["alarm_events_by_severity"] == dict(severity_counts)
    assert summary["severe_alarm_event_count"] == (
        severity_counts["high"] + severity_counts["critical"]
    )
    assert summary["nuisance_alarm_event_count"] == sum(
        severity_counts[severity] for severity in ("info", "low", "medium")
    )
    assert summary["acknowledgement_count"] == len(
        dataset.tables["acknowledgement"]
    )
    assert summary["timely_acknowledgement_count"] == timely_count
    assert summary["late_acknowledgement_count"] == late_count
    assert summary["excused_acknowledgement_count"] == excused_count
    assert summary["acknowledgement_before_alarm_count"] == before_count
    assert (
        summary["unacknowledged_required_alarm_event_count"]
        == unacknowledged_required_count
    )
    assert summary["acknowledgement_sla_exception_count"] == (
        late_count + before_count + unacknowledged_required_count
    )
    assert summary["acknowledgement_response_observation_count"] == len(
        response_seconds
    )
    assert summary["mean_acknowledgement_response_seconds"] == pytest.approx(
        sum(response_seconds) / len(response_seconds)
    )
    assert summary["repeated_alarm_cluster_count"] == len(clusters)
    assert summary["alarm_cluster_count_by_kind"] == {
        "nuisance": cluster_counts["nuisance"],
        "severe": cluster_counts["severe"],
    }
    assert summary["incident_count_by_status"] == dict(
        Counter(row["status"] for row in dataset.tables["incident"])
    )
    assert summary["corrective_action_count_by_status"] == dict(
        action_status_counts
    )
    assert summary["verified_corrective_action_count"] == sum(
        row["verification_result"] == "passed" for row in actions
    )
    assert summary["closed_unverified_corrective_action_count"] == sum(
        row["status"] == "closed" and row["verification_result"] != "passed"
        for row in actions
    )
    assert summary["incident_traceability"] == incident_traceability
    assert "ground_truth" not in summary
    assert len(dataset.replay_deliveries) == len(dataset.tables["alarm_event"]) + 1

    labels = {row["issue_type"]: row for row in dataset.ground_truth}
    contextless_incident_id = labels[
        "incident_without_triggering_context"
    ]["entity_id"]
    incident_evidence = api_client.get(
        f"/api/v1/incidents/{contextless_incident_id}/evidence"
    )
    assert incident_evidence.status_code == 200, incident_evidence.text
    traceability = incident_evidence.json()["traceability"]
    assert traceability["traceable"] is False
    assert traceability["exceptions"] == [
        "missing_source_alarm_events",
        "missing_triggering_machine_state",
    ]
