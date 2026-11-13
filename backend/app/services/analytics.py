from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, exists, func, select, text
from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement,
    AcknowledgementStatus,
    AlarmDefinition,
    AlarmEvent,
    CorrectiveAction,
    CorrectiveActionStatus,
    Incident,
    IncidentAlarmEvent,
    LifecycleEntityType,
    LifecycleHistory,
    MachineState,
    MachineStateEvent,
    VerificationResult,
)

INCIDENT_CONTEXT_WINDOW = timedelta(minutes=5)
UNSAFE_STATES = {MachineState.FAULT, MachineState.INTERLOCK_BYPASSED}

ALARM_CLUSTER_CTES = """
WITH lagged_events AS (
    SELECT
        id,
        machine_id,
        alarm_definition_id,
        event_at,
        lag(event_at) OVER (
            PARTITION BY machine_id, alarm_definition_id
            ORDER BY event_at, id
        ) AS previous_event_at
    FROM alarm_event
),
marked_events AS (
    SELECT
        id,
        machine_id,
        alarm_definition_id,
        event_at,
        CASE
            WHEN previous_event_at IS NULL
                OR event_at - previous_event_at > INTERVAL '1 minute'
            THEN 1
            ELSE 0
        END AS starts_cluster
    FROM lagged_events
),
grouped_events AS (
    SELECT
        id,
        machine_id,
        alarm_definition_id,
        event_at,
        sum(starts_cluster) OVER (
            PARTITION BY machine_id, alarm_definition_id
            ORDER BY event_at, id
            ROWS UNBOUNDED PRECEDING
        ) AS cluster_id
    FROM marked_events
),
alarm_clusters AS (
    SELECT
        machine_id,
        alarm_definition_id,
        cluster_id,
        count(*) AS event_count,
        min(event_at) AS starts_at,
        max(event_at) AS ends_at
    FROM grouped_events
    GROUP BY machine_id, alarm_definition_id, cluster_id
    HAVING count(*) >= 3
)
"""


def _status_counts(db: Session, model: Any, status_column: Any) -> dict[str, int]:
    rows = db.execute(
        select(status_column, func.count(model.id)).group_by(status_column)
    )
    return {
        status.value if hasattr(status, "value") else str(status): count
        for status, count in rows
    }


def _cluster_filter_sql() -> str:
    return """
        (CAST(:machine_id AS uuid) IS NULL OR c.machine_id = CAST(:machine_id AS uuid))
        AND (
            CAST(:severity AS varchar) IS NULL
            OR d.severity = CAST(:severity AS varchar)
        )
        AND (
            CAST(:kind AS varchar) IS NULL
            OR (
                CASE
                    WHEN d.severity IN ('info', 'low', 'medium') THEN 'nuisance'
                    ELSE 'severe'
                END
            ) = CAST(:kind AS varchar)
        )
        AND (
            CAST(:search AS varchar) IS NULL
            OR m.machine_code ILIKE CAST(:search AS varchar)
            OR d.alarm_code ILIKE CAST(:search AS varchar)
            OR d.name ILIKE CAST(:search AS varchar)
        )
    """


def _cluster_query_params(
    *,
    machine_id: UUID | None,
    severity: str | None,
    kind: str | None,
    search: str | None,
) -> dict[str, Any]:
    return {
        "machine_id": machine_id,
        "severity": severity,
        "kind": kind,
        "search": f"%{search.strip()}%" if search and search.strip() else None,
    }


def list_alarm_clusters(
    db: Session,
    *,
    machine_id: UUID | None = None,
    severity: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params = _cluster_query_params(
        machine_id=machine_id,
        severity=severity,
        kind=kind,
        search=search,
    )
    filters = _cluster_filter_sql()
    rows = db.execute(
        text(
            f"""
            {ALARM_CLUSTER_CTES},
            filtered_clusters AS (
                SELECT
                    c.machine_id,
                    m.machine_code,
                    c.alarm_definition_id,
                    d.alarm_code,
                    d.name AS alarm_name,
                    d.severity,
                    c.event_count,
                    c.starts_at,
                    c.ends_at,
                    count(*) OVER () AS total
                FROM alarm_clusters c
                JOIN alarm_definition d ON d.id = c.alarm_definition_id
                JOIN machine m ON m.id = c.machine_id
                WHERE {filters}
            )
            SELECT
                *
            FROM filtered_clusters
            ORDER BY starts_at DESC, machine_id, alarm_definition_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    if rows:
        total = int(rows[0]["total"])
        items = []
        for row in rows:
            item = dict(row)
            item.pop("total")
            items.append(item)
    else:
        total = int(
            db.scalar(
                text(
                    f"""
                    {ALARM_CLUSTER_CTES}
                    SELECT count(*)
                    FROM alarm_clusters c
                    JOIN alarm_definition d ON d.id = c.alarm_definition_id
                    JOIN machine m ON m.id = c.machine_id
                    WHERE {filters}
                    """
                ),
                params,
            )
            or 0
        )
        items = []
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _alarm_cluster_counts_by_kind(db: Session) -> dict[str, int]:
    rows = db.execute(
        text(
            f"""
            {ALARM_CLUSTER_CTES}
            SELECT
                CASE
                    WHEN d.severity IN ('info', 'low', 'medium') THEN 'nuisance'
                    ELSE 'severe'
                END AS kind,
                count(*) AS cluster_count
            FROM alarm_clusters c
            JOIN alarm_definition d ON d.id = c.alarm_definition_id
            GROUP BY kind
            """
        )
    )
    counts = {"nuisance": 0, "severe": 0}
    counts.update({kind: count for kind, count in rows})
    return counts


def _acknowledgement_metrics(db: Session) -> dict[str, Any]:
    elapsed = func.extract(
        "epoch",
        Acknowledgement.acknowledged_at - AlarmEvent.event_at,
    )
    (
        acknowledgement_count,
        timely_count,
        late_count,
        before_alarm_count,
        excused_count,
        mean_response,
        response_count,
    ) = db.execute(
        select(
            func.count(Acknowledgement.id),
            func.sum(
                case(
                    (
                        and_(
                            elapsed >= 0,
                            elapsed <= AlarmDefinition.ack_sla_seconds,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case((elapsed > AlarmDefinition.ack_sla_seconds, 1), else_=0)
            ),
            func.sum(case((elapsed < 0, 1), else_=0)),
            func.sum(
                case(
                    (Acknowledgement.status == AcknowledgementStatus.EXCUSED, 1),
                    else_=0,
                )
            ),
            func.avg(case((elapsed >= 0, elapsed), else_=None)),
            func.sum(case((elapsed >= 0, 1), else_=0)),
        )
        .join(AlarmEvent, AlarmEvent.id == Acknowledgement.alarm_event_id)
        .join(
            AlarmDefinition,
            AlarmDefinition.id == AlarmEvent.alarm_definition_id,
        )
    ).one()

    unacknowledged_required_count = int(
        db.scalar(
            select(func.count(AlarmEvent.id))
            .join(
                AlarmDefinition,
                AlarmDefinition.id == AlarmEvent.alarm_definition_id,
            )
            .where(
                AlarmDefinition.acknowledgement_required.is_(True),
                ~exists(
                    select(Acknowledgement.id).where(
                        Acknowledgement.alarm_event_id == AlarmEvent.id
                    )
                ),
            )
        )
        or 0
    )
    return {
        "acknowledgement_count": int(acknowledgement_count or 0),
        "timely_acknowledgement_count": int(timely_count or 0),
        "late_acknowledgement_count": int(late_count or 0),
        "excused_acknowledgement_count": int(excused_count or 0),
        "acknowledgement_before_alarm_count": int(before_alarm_count or 0),
        "unacknowledged_required_alarm_event_count": unacknowledged_required_count,
        "acknowledgement_sla_exception_count": (
            int(late_count or 0)
            + int(before_alarm_count or 0)
            + unacknowledged_required_count
        ),
        "mean_acknowledgement_response_seconds": (
            float(mean_response) if mean_response is not None else None
        ),
        "acknowledgement_response_observation_count": int(response_count or 0),
    }


def _linked_state_ids(history: list[Any]) -> set[str]:
    state_ids: set[str] = set()
    for item in history:
        payload = item.payload
        if not isinstance(payload, dict):
            continue
        direct_id = payload.get("machine_state_event_id")
        if direct_id:
            state_ids.add(str(direct_id))
        for context in payload.get("linked_contexts", []):
            if isinstance(context, dict) and context.get("machine_state_event_id"):
                state_ids.add(str(context["machine_state_event_id"]))
    return state_ids


def incident_traceability(
    incident: Any,
    alarm_events: list[Any],
    machine_state_events: list[Any],
    corrective_actions: list[Any],
    lifecycle_history: list[Any],
) -> dict[str, Any]:
    alarms_by_id = {str(event.id): event for event in alarm_events}
    states_by_id = {str(event.id): event for event in machine_state_events}
    declared_pairs = {
        (
            str(context.get("alarm_event_id")),
            str(context.get("machine_state_event_id")),
        )
        for history in lifecycle_history
        if isinstance(history.payload, dict)
        for context in history.payload.get("linked_contexts", [])
        if isinstance(context, dict)
        and context.get("alarm_event_id")
        and context.get("machine_state_event_id")
    }
    direct_state_ids = {
        str(history.payload["machine_state_event_id"])
        for history in lifecycle_history
        if isinstance(history.payload, dict)
        and history.payload.get("machine_state_event_id")
    }

    def valid_context(alarm: Any, state: Any) -> bool:
        return (
            alarm.machine_id == incident.machine_id
            and state.machine_id == incident.machine_id
            and state.state in UNSAFE_STATES
            and timedelta(0) <= alarm.event_at - state.event_at
            <= INCIDENT_CONTEXT_WINDOW
            and timedelta(0) <= incident.opened_at - alarm.event_at
            <= INCIDENT_CONTEXT_WINDOW
        )

    valid_context_pair = False
    for alarm_id, state_id in declared_pairs:
        alarm = alarms_by_id.get(alarm_id)
        state = states_by_id.get(state_id)
        if alarm is not None and state is not None and valid_context(alarm, state):
            valid_context_pair = True
            break
    if not valid_context_pair:
        valid_context_pair = any(
            state_id in states_by_id
            and any(
                valid_context(alarm, states_by_id[state_id])
                for alarm in alarm_events
            )
            for state_id in direct_state_ids
        )
    has_state_context = valid_context_pair
    incident_history = [
        history
        for history in lifecycle_history
        if history.entity_type == LifecycleEntityType.INCIDENT
        and history.entity_id == incident.id
    ]
    has_lifecycle = any(
        history.transition == "opened" for history in incident_history
    )
    actions_with_open_history = {
        history.entity_id
        for history in lifecycle_history
        if history.entity_type == LifecycleEntityType.CORRECTIVE_ACTION
        and history.transition == "opened"
    }
    actions_missing_history = [
        action
        for action in corrective_actions
        if action.id not in actions_with_open_history
    ]
    action_lifecycle_record_count = sum(
        history.entity_type == LifecycleEntityType.CORRECTIVE_ACTION
        for history in lifecycle_history
    )
    unverified_closed_action_count = sum(
        action.status == CorrectiveActionStatus.CLOSED
        and action.verification_result != VerificationResult.PASSED
        for action in corrective_actions
    )
    exceptions = []
    if not alarm_events:
        exceptions.append("missing_source_alarm_events")
    if not has_state_context:
        exceptions.append("missing_triggering_machine_state")
    if not has_lifecycle:
        exceptions.append("missing_lifecycle_history")
    if actions_missing_history:
        exceptions.append("missing_corrective_action_lifecycle_history")
    if unverified_closed_action_count:
        exceptions.append("closed_action_without_passed_verification")
    return {
        "traceable": not exceptions,
        "source_alarm_event_count": len(alarm_events),
        "machine_state_event_count": len(machine_state_events),
        "corrective_action_count": len(corrective_actions),
        "lifecycle_record_count": len(lifecycle_history),
        "action_lifecycle_record_count": action_lifecycle_record_count,
        "closed_unverified_action_count": unverified_closed_action_count,
        "exceptions": exceptions,
    }


def _incident_traceability_summary(db: Session) -> dict[str, int]:
    incidents = db.scalars(select(Incident)).all()
    if not incidents:
        return {
            "incident_count": 0,
            "traceable_incident_count": 0,
            "traceability_exception_incident_count": 0,
            "missing_alarm_context_incident_count": 0,
            "missing_machine_state_context_incident_count": 0,
            "missing_lifecycle_history_incident_count": 0,
            "missing_action_lifecycle_history_incident_count": 0,
            "closed_unverified_action_incident_count": 0,
        }

    incident_ids = [incident.id for incident in incidents]
    alarms_by_incident: dict[UUID, list[Any]] = defaultdict(list)
    for incident_id, alarm in db.execute(
        select(IncidentAlarmEvent.incident_id, AlarmEvent)
        .join(AlarmEvent, AlarmEvent.id == IncidentAlarmEvent.alarm_event_id)
        .where(IncidentAlarmEvent.incident_id.in_(incident_ids))
    ):
        alarms_by_incident[incident_id].append(alarm)

    actions_by_incident: dict[UUID, list[Any]] = defaultdict(list)
    for action in db.scalars(
        select(CorrectiveAction).where(
            CorrectiveAction.incident_id.in_(incident_ids)
        )
    ):
        actions_by_incident[action.incident_id].append(action)

    action_ids = [
        action.id
        for incident_actions in actions_by_incident.values()
        for action in incident_actions
    ]
    history_entity_ids = incident_ids + action_ids
    history_types = [
        LifecycleEntityType.INCIDENT,
        LifecycleEntityType.CORRECTIVE_ACTION,
    ]
    history_rows = db.scalars(
        select(LifecycleHistory).where(
            LifecycleHistory.entity_type.in_(history_types),
            LifecycleHistory.entity_id.in_(history_entity_ids),
        )
    ).all()
    history_by_incident: dict[UUID, list[Any]] = defaultdict(list)
    history_by_action: dict[UUID, list[Any]] = defaultdict(list)
    for history in history_rows:
        if history.entity_type == LifecycleEntityType.INCIDENT:
            history_by_incident[history.entity_id].append(history)
        elif history.entity_type == LifecycleEntityType.CORRECTIVE_ACTION:
            history_by_action[history.entity_id].append(history)

    linked_state_ids = set().union(
        *(
            _linked_state_ids(history_by_incident[incident_id])
            for incident_id in incident_ids
        )
    )
    states_by_incident: dict[UUID, list[Any]] = defaultdict(list)
    if linked_state_ids:
        states = db.scalars(
            select(MachineStateEvent).where(
                MachineStateEvent.id.in_(
                    [UUID(state_id) for state_id in linked_state_ids]
                )
            )
        ).all()
        states_by_id = {str(state.id): state for state in states}
        for incident_id in incident_ids:
            states_by_incident[incident_id] = [
                states_by_id[state_id]
                for state_id in _linked_state_ids(
                    history_by_incident[incident_id]
                )
                if state_id in states_by_id
            ]

    counts = {
        "incident_count": len(incidents),
        "traceable_incident_count": 0,
        "traceability_exception_incident_count": 0,
        "missing_alarm_context_incident_count": 0,
        "missing_machine_state_context_incident_count": 0,
        "missing_lifecycle_history_incident_count": 0,
        "missing_action_lifecycle_history_incident_count": 0,
        "closed_unverified_action_incident_count": 0,
    }
    for incident in incidents:
        result = incident_traceability(
            incident,
            alarms_by_incident[incident.id],
            states_by_incident[incident.id],
            actions_by_incident[incident.id],
            history_by_incident[incident.id]
            + [
                history
                for action in actions_by_incident[incident.id]
                for history in history_by_action[action.id]
            ],
        )
        if result["traceable"]:
            counts["traceable_incident_count"] += 1
        else:
            counts["traceability_exception_incident_count"] += 1
        for exception, count_key in (
            ("missing_source_alarm_events", "missing_alarm_context_incident_count"),
            (
                "missing_triggering_machine_state",
                "missing_machine_state_context_incident_count",
            ),
            ("missing_lifecycle_history", "missing_lifecycle_history_incident_count"),
            (
                "missing_corrective_action_lifecycle_history",
                "missing_action_lifecycle_history_incident_count",
            ),
            (
                "closed_action_without_passed_verification",
                "closed_unverified_action_incident_count",
            ),
        ):
            if exception in result["exceptions"]:
                counts[count_key] += 1
    return counts


def get_summary(db: Session) -> dict[str, Any]:
    severity_rows = db.execute(
        select(AlarmDefinition.severity, func.count(AlarmEvent.id))
        .join(AlarmEvent, AlarmEvent.alarm_definition_id == AlarmDefinition.id)
        .group_by(AlarmDefinition.severity)
    ).all()
    alarm_event_count = sum(count for _, count in severity_rows)
    alarm_events_by_severity = {
        severity.value if hasattr(severity, "value") else str(severity): count
        for severity, count in severity_rows
    }
    severe_alarm_event_count = sum(
        alarm_events_by_severity.get(severity, 0)
        for severity in ("high", "critical")
    )
    nuisance_alarm_event_count = sum(
        alarm_events_by_severity.get(severity, 0)
        for severity in ("info", "low", "medium")
    )
    acknowledgement_metrics = _acknowledgement_metrics(db)
    action_status_counts = _status_counts(
        db, CorrectiveAction, CorrectiveAction.status
    )
    cluster_counts_by_kind = _alarm_cluster_counts_by_kind(db)
    return {
        "alarm_event_count": alarm_event_count,
        "alarm_events_by_severity": alarm_events_by_severity,
        "severe_alarm_event_count": severe_alarm_event_count,
        "nuisance_alarm_event_count": nuisance_alarm_event_count,
        **acknowledgement_metrics,
        "incident_count_by_status": _status_counts(
            db, Incident, Incident.status
        ),
        "corrective_action_count_by_status": action_status_counts,
        "verified_corrective_action_count": int(
            db.scalar(
                select(func.count(CorrectiveAction.id)).where(
                    CorrectiveAction.verification_result
                    == VerificationResult.PASSED
                )
            )
            or 0
        ),
        "closed_unverified_corrective_action_count": int(
            db.scalar(
                select(func.count(CorrectiveAction.id)).where(
                    CorrectiveAction.status == CorrectiveActionStatus.CLOSED,
                    CorrectiveAction.verification_result
                    != VerificationResult.PASSED,
                )
            )
            or 0
        ),
        "repeated_alarm_cluster_count": sum(cluster_counts_by_kind.values()),
        "alarm_cluster_count_by_kind": cluster_counts_by_kind,
        "incident_traceability": _incident_traceability_summary(db),
    }
