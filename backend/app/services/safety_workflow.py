from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
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
    IncidentStatus,
    Interlock,
    InterlockStatus,
    LifecycleEntityType,
    LifecycleHistory,
    Machine,
    MachineState,
    MachineStateEvent,
    AlarmDefinitionStatus,
    VerificationResult,
    OperatorShift,
)


INCIDENT_CONTEXT_WINDOW = timedelta(minutes=5)
SEVERE_ALARM_LEVELS = {"high", "critical"}
UNSAFE_MACHINE_STATES = {
    MachineState.FAULT,
    MachineState.INTERLOCK_BYPASSED,
}
INCIDENT_ADVISORY_LOCK_NAMESPACE = 741923

INCIDENT_TRANSITIONS = {
    IncidentStatus.OPEN: {IncidentStatus.INVESTIGATING},
    IncidentStatus.INVESTIGATING: {IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: {IncidentStatus.CLOSED},
    IncidentStatus.CLOSED: set(),
}
ACTION_TRANSITIONS = {
    CorrectiveActionStatus.OPEN: {
        CorrectiveActionStatus.IN_PROGRESS,
        CorrectiveActionStatus.CANCELLED,
    },
    CorrectiveActionStatus.IN_PROGRESS: {
        CorrectiveActionStatus.COMPLETED,
        CorrectiveActionStatus.CANCELLED,
    },
    CorrectiveActionStatus.COMPLETED: set(),
    CorrectiveActionStatus.CLOSED: set(),
    CorrectiveActionStatus.CANCELLED: set(),
}


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _is_effective(record: Any, at: datetime) -> bool:
    return record.effective_from <= at and (
        record.effective_to is None or at < record.effective_to
    )


def _append_history(
    db: Session,
    *,
    entity_type: LifecycleEntityType,
    entity_id: uuid.UUID,
    transition: str,
    occurred_at: datetime,
    actor_reference: str | None,
    payload: dict[str, Any],
    reason: str | None = None,
) -> None:
    db.add(
        LifecycleHistory(
            id=uuid.uuid4(),
            entity_type=entity_type,
            entity_id=entity_id,
            transition=transition,
            occurred_at=occurred_at,
            actor_reference=actor_reference,
            reason=reason,
            payload=payload,
        )
    )


def _lock_machine(db: Session, machine_id: uuid.UUID) -> Machine:
    machine = db.scalar(
        select(Machine).where(Machine.id == machine_id).with_for_update()
    )
    if machine is None:
        raise HTTPException(status_code=404, detail="machine not found")
    return machine


def _ensure_event_order(
    db: Session,
    machine_id: uuid.UUID,
    event_at: datetime,
) -> None:
    latest_alarm_at = db.scalar(
        select(AlarmEvent.event_at)
        .where(AlarmEvent.machine_id == machine_id)
        .order_by(AlarmEvent.event_at.desc(), AlarmEvent.id.desc())
        .limit(1)
    )
    latest_state_at = db.scalar(
        select(MachineStateEvent.event_at)
        .where(MachineStateEvent.machine_id == machine_id)
        .order_by(MachineStateEvent.event_at.desc(), MachineStateEvent.id.desc())
        .limit(1)
    )
    previous_times = [
        value for value in (latest_alarm_at, latest_state_at) if value is not None
    ]
    if previous_times and event_at < max(previous_times):
        raise HTTPException(
            status_code=422,
            detail="event_at predates the latest recorded machine event",
        )


def _find_unsafe_state(
    db: Session,
    machine_id: uuid.UUID,
    alarm_at: datetime,
) -> MachineStateEvent | None:
    return db.scalar(
        select(MachineStateEvent)
        .where(
            MachineStateEvent.machine_id == machine_id,
            MachineStateEvent.state.in_(UNSAFE_MACHINE_STATES),
            MachineStateEvent.event_at <= alarm_at,
            MachineStateEvent.event_at >= alarm_at - INCIDENT_CONTEXT_WINDOW,
        )
        .order_by(MachineStateEvent.event_at.desc(), MachineStateEvent.id.desc())
        .limit(1)
    )


def _next_incident_business_id(db: Session, year: int) -> str:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :year)"),
        {"namespace": INCIDENT_ADVISORY_LOCK_NAMESPACE, "year": year},
    )
    prefix = f"INC-{year}-"
    latest = db.scalar(
        select(Incident.business_id)
        .where(Incident.business_id.like(f"{prefix}%"))
        .order_by(Incident.business_id.desc())
        .limit(1)
    )
    next_number = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
    return f"{prefix}{next_number:06d}"


def _create_incident_for_alarm(
    db: Session,
    alarm: AlarmEvent,
    state_event: MachineStateEvent,
    *,
    business_id: str | None = None,
    opened_at: datetime | None = None,
    actor_reference: str = "safety_rule",
) -> Incident:
    incident_opened_at = opened_at or (
        alarm.event_at + timedelta(seconds=1)
    )
    generated_business_id = business_id or _next_incident_business_id(
        db, incident_opened_at.year
    )
    incident = Incident(
        id=uuid.uuid4(),
        business_id=generated_business_id,
        machine_id=alarm.machine_id,
        opened_at=incident_opened_at,
        status=IncidentStatus.OPEN,
        issue_type="unsafe_condition",
        created_at=incident_opened_at,
        updated_at=incident_opened_at,
    )
    db.add(incident)
    db.flush()
    db.add(
        IncidentAlarmEvent(
            id=uuid.uuid4(),
            incident_id=incident.id,
            alarm_event_id=alarm.id,
            linked_at=incident_opened_at,
        )
    )
    _append_history(
        db,
        entity_type=LifecycleEntityType.INCIDENT,
        entity_id=incident.id,
        transition="opened",
        occurred_at=incident_opened_at,
        actor_reference=actor_reference,
        payload={
            "status": IncidentStatus.OPEN.value,
            "alarm_event_id": str(alarm.id),
            "machine_state_event_id": str(state_event.id),
        },
    )
    db.flush()
    return incident


def _incident_for_alarm(
    db: Session,
    alarm_event_id: uuid.UUID,
) -> Incident | None:
    return db.scalar(
        select(Incident)
        .join(IncidentAlarmEvent, IncidentAlarmEvent.incident_id == Incident.id)
        .where(IncidentAlarmEvent.alarm_event_id == alarm_event_id)
        .order_by(Incident.opened_at, Incident.id)
        .limit(1)
    )


def _same_alarm_payload(
    event: AlarmEvent,
    machine_id: uuid.UUID,
    alarm_definition_id: uuid.UUID,
    event_at: datetime,
) -> bool:
    return (
        event.machine_id == machine_id
        and event.alarm_definition_id == alarm_definition_id
        and event.event_at == event_at
    )


def _alarm_response(
    db: Session,
    event: AlarmEvent,
    definition: AlarmDefinition,
    *,
    idempotent: bool,
    incident_created: bool,
    unsafe_context: bool,
) -> dict[str, Any]:
    incident = _incident_for_alarm(db, event.id)
    exceptions = []
    if _enum_value(definition.severity) in SEVERE_ALARM_LEVELS and not unsafe_context:
        exceptions.append("no_unsafe_machine_state_within_five_minutes")
    return {
        "event": event,
        "idempotent": idempotent,
        "decision": {
            "severity": definition.severity,
            "acknowledgement_required": definition.acknowledgement_required,
            "ack_sla_seconds": definition.ack_sla_seconds,
            "unsafe_context": unsafe_context,
            "incident_created": incident_created,
            "incident_id": incident.id if incident else None,
            "exceptions": exceptions,
        },
    }


def ingest_alarm_event(db: Session, payload: Any) -> dict[str, Any]:
    try:
        with db.begin():
            existing = db.scalar(
                select(AlarmEvent)
                .where(AlarmEvent.external_event_id == payload.external_event_id)
                .with_for_update()
            )
            if existing is not None:
                definition = db.get(AlarmDefinition, existing.alarm_definition_id)
                if definition is None or not _same_alarm_payload(
                    existing,
                    payload.machine_id,
                    payload.alarm_definition_id,
                    payload.event_at,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="external_event_id is already used by a different alarm",
                    )
                state_event = _find_unsafe_state(
                    db, existing.machine_id, existing.event_at
                )
                return _alarm_response(
                    db,
                    existing,
                    definition,
                    idempotent=True,
                    incident_created=False,
                    unsafe_context=state_event is not None,
                )

            if payload.received_at < payload.event_at:
                raise HTTPException(
                    status_code=422,
                    detail="received_at cannot precede event_at",
                )
            machine = _lock_machine(db, payload.machine_id)
            if not _is_effective(machine, payload.event_at):
                raise HTTPException(
                    status_code=422,
                    detail="machine is not effective at event_at",
                )
            definition = db.get(AlarmDefinition, payload.alarm_definition_id)
            if (
                definition is None
                or definition.machine_id != machine.id
                or not _is_effective(definition, payload.event_at)
                or definition.status != AlarmDefinitionStatus.ACTIVE
            ):
                raise HTTPException(
                    status_code=422,
                    detail="alarm definition is not active for this machine at event_at",
                )
            _ensure_event_order(db, machine.id, payload.event_at)
            event = AlarmEvent(
                id=uuid.uuid4(),
                machine_id=machine.id,
                alarm_definition_id=definition.id,
                external_event_id=payload.external_event_id,
                event_at=payload.event_at,
                received_at=payload.received_at,
            )
            db.add(event)
            db.flush()
            _append_history(
                db,
                entity_type=LifecycleEntityType.ALARM_EVENT,
                entity_id=event.id,
                transition="ingested",
                occurred_at=payload.received_at,
                actor_reference="event_ingestion",
                payload={"external_event_id": event.external_event_id},
            )

            state_event = _find_unsafe_state(db, machine.id, event.event_at)
            incident = None
            if (
                _enum_value(definition.severity) in SEVERE_ALARM_LEVELS
                and state_event is not None
            ):
                incident = _create_incident_for_alarm(
                    db,
                    event,
                    state_event,
                    actor_reference="deterministic_safety_rule",
                )
            db.flush()
            result = _alarm_response(
                db,
                event,
                definition,
                idempotent=False,
                incident_created=incident is not None,
                unsafe_context=state_event is not None,
            )
        return result
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(AlarmEvent).where(
                AlarmEvent.external_event_id == payload.external_event_id
            )
        )
        if existing is not None and _same_alarm_payload(
            existing,
            payload.machine_id,
            payload.alarm_definition_id,
            payload.event_at,
        ):
            definition = db.get(AlarmDefinition, existing.alarm_definition_id)
            if definition is not None:
                return _alarm_response(
                    db,
                    existing,
                    definition,
                    idempotent=True,
                    incident_created=False,
                    unsafe_context=(
                        _find_unsafe_state(db, existing.machine_id, existing.event_at)
                        is not None
                    ),
                )
        raise HTTPException(
            status_code=409,
            detail="alarm event conflicts with an existing immutable record",
        ) from exc


def _same_state_payload(event: MachineStateEvent, payload: Any) -> bool:
    return (
        event.machine_id == payload.machine_id
        and event.interlock_id == payload.interlock_id
        and event.state == payload.state
        and event.interlock_state == payload.interlock_state
        and event.event_at == payload.event_at
    )


def ingest_machine_state_event(
    db: Session,
    payload: Any,
) -> dict[str, Any]:
    try:
        with db.begin():
            existing = db.scalar(
                select(MachineStateEvent)
                .where(MachineStateEvent.source_event_id == payload.source_event_id)
                .with_for_update()
            )
            if existing is not None:
                if not _same_state_payload(existing, payload):
                    raise HTTPException(
                        status_code=409,
                        detail="source_event_id is already used by a different machine state",
                    )
                return {
                    "event": existing,
                    "idempotent": True,
                    "decision": "duplicate_source_event_ignored",
                    "exceptions": [],
                }

            machine = _lock_machine(db, payload.machine_id)
            if not _is_effective(machine, payload.event_at):
                raise HTTPException(
                    status_code=422,
                    detail="machine is not effective at event_at",
                )
            if payload.interlock_id is not None:
                interlock = db.get(Interlock, payload.interlock_id)
                if (
                    interlock is None
                    or interlock.machine_id != machine.id
                    or not _is_effective(interlock, payload.event_at)
                    or interlock.status != InterlockStatus.ACTIVE
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="interlock is not effective for this machine at event_at",
                    )
                if (
                    payload.interlock_state is not None
                    and payload.interlock_state != interlock.required_state
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="interlock_state conflicts with configured required_state",
                    )
            elif payload.interlock_state is not None:
                raise HTTPException(
                    status_code=422,
                    detail="interlock_state requires interlock_id",
                )
            _ensure_event_order(db, machine.id, payload.event_at)

            event = MachineStateEvent(
                id=uuid.uuid4(),
                machine_id=machine.id,
                interlock_id=payload.interlock_id,
                state=payload.state,
                interlock_state=payload.interlock_state,
                event_at=payload.event_at,
                source_event_id=payload.source_event_id,
            )
            db.add(event)
            db.flush()
            _append_history(
                db,
                entity_type=LifecycleEntityType.MACHINE_STATE_EVENT,
                entity_id=event.id,
                transition="state_observed",
                occurred_at=event.event_at,
                actor_reference=payload.actor_reference,
                payload={
                    "state": _enum_value(event.state),
                    "interlock_state": (
                        _enum_value(event.interlock_state)
                        if event.interlock_state is not None
                        else None
                    ),
                    "source_event_id": event.source_event_id,
                },
            )
            db.flush()
            return {
                "event": event,
                "idempotent": False,
                "decision": "state_event_recorded",
                "exceptions": [],
            }
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(MachineStateEvent).where(
                MachineStateEvent.source_event_id == payload.source_event_id
            )
        )
        if existing is not None and _same_state_payload(existing, payload):
            return {
                "event": existing,
                "idempotent": True,
                "decision": "duplicate_source_event_ignored",
                "exceptions": [],
            }
        raise HTTPException(
            status_code=409,
            detail="machine state conflicts with an existing immutable record",
        ) from exc


def record_acknowledgement(
    db: Session,
    payload: Any,
) -> dict[str, Any]:
    with db.begin():
        existing = db.scalar(
            select(Acknowledgement)
            .where(Acknowledgement.business_id == payload.business_id)
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.alarm_event_id != payload.alarm_event_id
                or existing.operator_shift_id != payload.operator_shift_id
                or existing.acknowledged_at != payload.acknowledged_at
            ):
                raise HTTPException(
                    status_code=409,
                    detail="acknowledgement business_id conflicts with an existing record",
                )
            definition = db.scalar(
                select(AlarmDefinition)
                .join(AlarmEvent, AlarmEvent.alarm_definition_id == AlarmDefinition.id)
                .where(AlarmEvent.id == existing.alarm_event_id)
            )
            late_by = max(
                0,
                int(
                    (
                        existing.acknowledged_at
                        - db.get(AlarmEvent, existing.alarm_event_id).event_at
                    ).total_seconds()
                    - definition.ack_sla_seconds
                ),
            )
            return {
                "acknowledgement": existing,
                "idempotent": True,
                "sla_seconds": definition.ack_sla_seconds,
                "late_by_seconds": late_by,
            }

        alarm = db.get(AlarmEvent, payload.alarm_event_id)
        if alarm is None:
            raise HTTPException(status_code=404, detail="alarm event not found")
        definition = db.get(AlarmDefinition, alarm.alarm_definition_id)
        if definition is None or not definition.acknowledgement_required:
            raise HTTPException(
                status_code=422,
                detail="alarm definition does not require acknowledgement",
            )
        if payload.acknowledged_at < alarm.event_at:
            raise HTTPException(
                status_code=422,
                detail="acknowledged_at cannot precede alarm event_at",
            )
        shift = db.get(OperatorShift, payload.operator_shift_id)
        if shift is None:
            raise HTTPException(status_code=404, detail="operator shift not found")
        if not (shift.starts_at <= payload.acknowledged_at < shift.ends_at):
            raise HTTPException(
                status_code=422,
                detail="acknowledged_at must fall within the operator shift",
            )
        expected_prefix = f"ACK-{alarm.external_event_id}-"
        if not re.fullmatch(re.escape(expected_prefix) + r"\d{2}", payload.business_id):
            raise HTTPException(
                status_code=422,
                detail="business_id must follow ACK-<alarm-event-id>-NN",
            )
        elapsed = (payload.acknowledged_at - alarm.event_at).total_seconds()
        late_by = max(0, int(elapsed - definition.ack_sla_seconds))
        status = (
            AcknowledgementStatus.LATE
            if elapsed > definition.ack_sla_seconds
            else AcknowledgementStatus.ACKNOWLEDGED
        )
        acknowledgement = Acknowledgement(
            id=uuid.uuid4(),
            business_id=payload.business_id,
            alarm_event_id=alarm.id,
            operator_shift_id=shift.id,
            acknowledged_at=payload.acknowledged_at,
            status=status,
            created_at=payload.acknowledged_at,
        )
        db.add(acknowledgement)
        db.flush()
        _append_history(
            db,
            entity_type=LifecycleEntityType.ACKNOWLEDGEMENT,
            entity_id=acknowledgement.id,
            transition=(
                "acknowledged_late"
                if status == AcknowledgementStatus.LATE
                else "acknowledged"
            ),
            occurred_at=acknowledgement.acknowledged_at,
            actor_reference=payload.actor_reference,
            payload={
                "status": status.value,
                "alarm_event_id": str(alarm.id),
                "sla_seconds": definition.ack_sla_seconds,
            },
        )
        db.flush()
        return {
            "acknowledgement": acknowledgement,
            "idempotent": False,
            "sla_seconds": definition.ack_sla_seconds,
            "late_by_seconds": late_by,
        }


def _validate_incident_business_id(business_id: str, opened_at: datetime) -> None:
    match = re.fullmatch(r"INC-(\d{4})-(\d{6})", business_id)
    if match is None or int(match.group(1)) != opened_at.year:
        raise HTTPException(
            status_code=422,
            detail="business_id must follow INC-<opened-year>-NNNNNN",
        )


def _load_incident_context(
    db: Session,
    machine_id: uuid.UUID,
    alarm_event_ids: list[uuid.UUID],
    opened_at: datetime,
) -> list[tuple[AlarmEvent, MachineStateEvent]]:
    alarm_rows = db.execute(
        select(AlarmEvent, AlarmDefinition)
        .join(AlarmDefinition, AlarmDefinition.id == AlarmEvent.alarm_definition_id)
        .where(AlarmEvent.id.in_(alarm_event_ids))
    ).all()
    alarms_by_id = {
        alarm.id: (alarm, definition) for alarm, definition in alarm_rows
    }
    state_windows = [
        and_(
            MachineStateEvent.event_at >= alarm.event_at - INCIDENT_CONTEXT_WINDOW,
            MachineStateEvent.event_at <= alarm.event_at,
        )
        for alarm, _ in alarms_by_id.values()
        if alarm.machine_id == machine_id
    ]
    state_events = (
        db.scalars(
            select(MachineStateEvent)
            .where(
                MachineStateEvent.machine_id == machine_id,
                MachineStateEvent.state.in_(UNSAFE_MACHINE_STATES),
                or_(*state_windows),
            )
            .order_by(MachineStateEvent.event_at.desc(), MachineStateEvent.id.desc())
        ).all()
        if state_windows
        else []
    )

    contexts: list[tuple[AlarmEvent, MachineStateEvent]] = []
    for alarm_event_id in alarm_event_ids:
        alarm_context = alarms_by_id.get(alarm_event_id)
        if alarm_context is None:
            raise HTTPException(status_code=404, detail="linked alarm event not found")
        alarm, definition = alarm_context
        if alarm.machine_id != machine_id:
            raise HTTPException(
                status_code=422,
                detail="incident and linked alarm events must reference the same machine",
            )
        if not alarm.event_at <= opened_at <= alarm.event_at + INCIDENT_CONTEXT_WINDOW:
            raise HTTPException(
                status_code=422,
                detail="incident opened_at must follow linked alarm within five minutes",
            )
        if (
            definition is None
            or _enum_value(definition.severity) not in SEVERE_ALARM_LEVELS
        ):
            raise HTTPException(
                status_code=422,
                detail="incident source alarm must be high or critical severity",
            )
        state_event = next(
            (
                candidate
                for candidate in state_events
                if alarm.event_at - INCIDENT_CONTEXT_WINDOW
                <= candidate.event_at
                <= alarm.event_at
            ),
            None,
        )
        if state_event is None:
            raise HTTPException(
                status_code=422,
                detail="linked alarm lacks a preceding fault or interlock-bypassed state",
            )
        contexts.append((alarm, state_event))
    return contexts


def create_incident(db: Session, payload: Any) -> dict[str, Any]:
    _validate_incident_business_id(payload.business_id, payload.opened_at)
    with db.begin():
        existing = db.scalar(
            select(Incident)
            .where(Incident.business_id == payload.business_id)
            .with_for_update()
        )
        if existing is not None:
            existing_links = set(
                db.scalars(
                    select(IncidentAlarmEvent.alarm_event_id).where(
                        IncidentAlarmEvent.incident_id == existing.id
                    )
                )
            )
            if (
                existing.machine_id != payload.machine_id
                or existing.opened_at != payload.opened_at
                or existing_links != set(payload.alarm_event_ids)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="incident business_id conflicts with an existing record",
                )
            return {
                "incident": existing,
                "idempotent": True,
                "linked_alarm_event_ids": sorted(existing_links, key=str),
            }

        _lock_machine(db, payload.machine_id)
        contexts = _load_incident_context(
            db,
            payload.machine_id,
            payload.alarm_event_ids,
            payload.opened_at,
        )
        incident = Incident(
            id=uuid.uuid4(),
            business_id=payload.business_id,
            machine_id=payload.machine_id,
            opened_at=payload.opened_at,
            status=IncidentStatus.OPEN,
            issue_type=payload.issue_type,
            created_at=payload.opened_at,
            updated_at=payload.opened_at,
        )
        db.add(incident)
        db.flush()
        for alarm, _ in contexts:
            db.add(
                IncidentAlarmEvent(
                    id=uuid.uuid4(),
                    incident_id=incident.id,
                    alarm_event_id=alarm.id,
                    linked_at=payload.opened_at,
                )
            )
        _append_history(
            db,
            entity_type=LifecycleEntityType.INCIDENT,
            entity_id=incident.id,
            transition="opened",
            occurred_at=payload.opened_at,
            actor_reference=payload.actor_reference,
            payload={
                "status": IncidentStatus.OPEN.value,
                "linked_contexts": [
                    {
                        "alarm_event_id": str(alarm.id),
                        "machine_state_event_id": str(state_event.id),
                    }
                    for alarm, state_event in contexts
                ],
            },
        )
        db.flush()
        return {
            "incident": incident,
            "idempotent": False,
            "linked_alarm_event_ids": [alarm.id for alarm, _ in contexts],
        }


def transition_incident(
    db: Session,
    incident_id: uuid.UUID,
    payload: Any,
) -> dict[str, Any]:
    with db.begin():
        incident = db.scalar(
            select(Incident)
            .where(Incident.id == incident_id)
            .with_for_update()
        )
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        if payload.status == incident.status:
            return {"incident": incident, "idempotent": True}
        if payload.status not in INCIDENT_TRANSITIONS[incident.status]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"incident cannot transition from {incident.status.value} "
                    f"to {payload.status.value}"
                ),
            )
        if payload.occurred_at < incident.updated_at:
            raise HTTPException(
                status_code=422,
                detail="incident transition timestamp predates its current lifecycle state",
            )
        if payload.status in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}:
            actions = db.scalars(
                select(CorrectiveAction).where(
                    CorrectiveAction.incident_id == incident.id
                )
            ).all()
            if not actions or any(
                action.status != CorrectiveActionStatus.CLOSED
                or action.verification_result != VerificationResult.PASSED
                for action in actions
            ):
                raise HTTPException(
                    status_code=409,
                    detail="incident cannot be resolved or closed before all actions are verified",
                )
        previous_status = incident.status
        incident.status = payload.status
        incident.updated_at = payload.occurred_at
        _append_history(
            db,
            entity_type=LifecycleEntityType.INCIDENT,
            entity_id=incident.id,
            transition=payload.status.value,
            occurred_at=payload.occurred_at,
            actor_reference=payload.actor_reference,
            payload={
                "from_status": previous_status.value,
                "status": payload.status.value,
            },
            reason=payload.reason,
        )
        db.flush()
        return {"incident": incident, "idempotent": False}


def _validate_action_business_id(business_id: str, incident: Incident) -> None:
    expected_prefix = f"ACT-{incident.business_id}-"
    if not re.fullmatch(re.escape(expected_prefix) + r"\d{2}", business_id):
        raise HTTPException(
            status_code=422,
            detail="business_id must follow ACT-<incident-business-id>-NN",
        )


def create_corrective_action(db: Session, payload: Any) -> dict[str, Any]:
    with db.begin():
        incident = db.scalar(
            select(Incident)
            .where(Incident.id == payload.incident_id)
            .with_for_update()
        )
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        _validate_action_business_id(payload.business_id, incident)
        existing = db.scalar(
            select(CorrectiveAction)
            .where(CorrectiveAction.business_id == payload.business_id)
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.incident_id != payload.incident_id
                or existing.owner != payload.owner
                or existing.due_at != payload.due_at
                or existing.created_at != payload.created_at
            ):
                raise HTTPException(
                    status_code=409,
                    detail="corrective action business_id conflicts with an existing record",
                )
            return {"action": existing, "idempotent": True}
        if incident.status in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}:
            raise HTTPException(
                status_code=409,
                detail="cannot add a corrective action to a resolved or closed incident",
            )
        if payload.created_at < incident.opened_at:
            raise HTTPException(
                status_code=422,
                detail="corrective action cannot be created before its incident",
            )
        action = CorrectiveAction(
            id=uuid.uuid4(),
            business_id=payload.business_id,
            incident_id=incident.id,
            owner=payload.owner,
            due_at=payload.due_at,
            status=CorrectiveActionStatus.OPEN,
            verification_result=VerificationResult.NOT_STARTED,
            created_at=payload.created_at,
            updated_at=payload.created_at,
        )
        db.add(action)
        db.flush()
        _append_history(
            db,
            entity_type=LifecycleEntityType.CORRECTIVE_ACTION,
            entity_id=action.id,
            transition="opened",
            occurred_at=payload.created_at,
            actor_reference=payload.actor_reference,
            payload={"status": CorrectiveActionStatus.OPEN.value},
        )
        db.flush()
        return {"action": action, "idempotent": False}


def transition_corrective_action(
    db: Session,
    action_id: uuid.UUID,
    payload: Any,
) -> dict[str, Any]:
    with db.begin():
        action = db.scalar(
            select(CorrectiveAction)
            .where(CorrectiveAction.id == action_id)
            .with_for_update()
        )
        if action is None:
            raise HTTPException(status_code=404, detail="corrective action not found")
        if payload.status == action.status:
            return {"action": action, "idempotent": True}
        if payload.status not in ACTION_TRANSITIONS[action.status]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"corrective action cannot transition from {action.status.value} "
                    f"to {payload.status.value}; closure requires verification"
                ),
            )
        if payload.occurred_at < action.updated_at:
            raise HTTPException(
                status_code=422,
                detail="action transition timestamp predates its current lifecycle state",
            )
        if (
            payload.status == CorrectiveActionStatus.COMPLETED
            and payload.occurred_at < action.created_at
        ):
            raise HTTPException(
                status_code=422,
                detail="action cannot be completed before it was created",
            )
        previous_status = action.status
        action.status = payload.status
        action.updated_at = payload.occurred_at
        transition = payload.status.value
        if payload.status == CorrectiveActionStatus.COMPLETED:
            action.completed_at = payload.occurred_at
        _append_history(
            db,
            entity_type=LifecycleEntityType.CORRECTIVE_ACTION,
            entity_id=action.id,
            transition=transition,
            occurred_at=payload.occurred_at,
            actor_reference=payload.actor_reference,
            payload={
                "from_status": previous_status.value,
                "status": payload.status.value,
            },
            reason=payload.reason,
        )
        db.flush()
        return {"action": action, "idempotent": False}


def verify_corrective_action(
    db: Session,
    action_id: uuid.UUID,
    payload: Any,
) -> dict[str, Any]:
    with db.begin():
        action = db.scalar(
            select(CorrectiveAction)
            .where(CorrectiveAction.id == action_id)
            .with_for_update()
        )
        if action is None:
            raise HTTPException(status_code=404, detail="corrective action not found")
        result = VerificationResult(payload.result)
        if action.status == CorrectiveActionStatus.CLOSED:
            same_verification = (
                action.verification_result == result == VerificationResult.PASSED
                and action.verification_evidence == payload.evidence
                and action.updated_at == payload.verified_at
            )
            if same_verification:
                return {"action": action, "idempotent": True}
            raise HTTPException(
                status_code=409,
                detail="closed corrective action cannot be verified again",
            )
        if action.status != CorrectiveActionStatus.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail="corrective action must be completed before verification",
            )
        if action.completed_at is None or payload.verified_at < action.completed_at:
            raise HTTPException(
                status_code=422,
                detail="verification timestamp must follow action completion",
            )
        if (
            action.verification_result == result
            and action.verification_evidence == payload.evidence
            and action.updated_at == payload.verified_at
        ):
            return {"action": action, "idempotent": True}

        action.verification_result = result
        action.verification_evidence = payload.evidence
        action.updated_at = payload.verified_at
        transition = (
            "verified"
            if result == VerificationResult.PASSED
            else "verification_failed"
        )
        if result == VerificationResult.PASSED:
            action.status = CorrectiveActionStatus.CLOSED
        _append_history(
            db,
            entity_type=LifecycleEntityType.CORRECTIVE_ACTION,
            entity_id=action.id,
            transition=transition,
            occurred_at=payload.verified_at,
            actor_reference=payload.actor_reference,
            payload={
                "verification_result": result.value,
                "evidence": payload.evidence,
            },
        )
        if result == VerificationResult.PASSED:
            _append_history(
                db,
                entity_type=LifecycleEntityType.CORRECTIVE_ACTION,
                entity_id=action.id,
                transition="closed",
                occurred_at=payload.verified_at,
                actor_reference=payload.actor_reference,
                payload={
                    "status": CorrectiveActionStatus.CLOSED.value,
                    "verification_result": result.value,
                },
            )
        db.flush()
        return {"action": action, "idempotent": False}
