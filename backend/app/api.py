from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Acknowledgement,
    AcknowledgementStatus,
    AlarmDefinition,
    AlarmDefinitionStatus,
    AlarmEvent,
    AlarmEventStatus,
    AlarmSeverity,
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
    MachineSafetyZone,
    MachineStateEvent,
    MachineStatus,
    OperatorShift,
    Plant,
    PlantStatus,
    SafetyZone,
    SafetyZoneStatus,
)
from app.schemas import (
    AlarmClusterRead,
    AcknowledgementCreate,
    AcknowledgementRead,
    AcknowledgementResult,
    AlarmDefinitionRead,
    AlarmEventCreate,
    AlarmEventRead,
    AlarmIngestionResult,
    AnalyticsSummary,
    CorrectiveActionCreate,
    CorrectiveActionRead,
    CorrectiveActionResult,
    CorrectiveActionTransitionRequest,
    CorrectiveActionVerificationRequest,
    IncidentCreate,
    IncidentEvidence,
    IncidentRead,
    IncidentResult,
    IncidentTransitionRequest,
    InterlockRead,
    LifecycleHistoryRead,
    MachineRead,
    MachineStateEventCreate,
    MachineStateEventRead,
    MachineStateIngestionResult,
    OperatorShiftRead,
    Page,
    PlantRead,
    SafetyZoneRead,
)
from app.services import analytics, safety_workflow


router = APIRouter(prefix="/api/v1", tags=["safety-workflow"])
MAX_PAGE_SIZE = 200


def _validate_time_range(
    from_at: datetime | None,
    to_at: datetime | None,
) -> None:
    if from_at is not None and to_at is not None and from_at >= to_at:
        raise HTTPException(
            status_code=422,
            detail="from_at must be earlier than to_at",
        )


def _paginate(
    db: Session,
    statement: Any,
    count_statement: Any,
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    total = db.scalar(count_statement) or 0
    items = db.scalars(statement.limit(limit).offset(offset)).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _search_pattern(search: str | None) -> str | None:
    term = search.strip() if search is not None else ""
    return f"%{term}%" if term else None


@router.get("/plants", response_model=Page[PlantRead])
def list_plants(
    status: PlantStatus | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(Plant)
    count_statement = select(func.count(Plant.id))
    if status is not None:
        statement = statement.where(Plant.status == status)
        count_statement = count_statement.where(Plant.status == status)
    return _paginate(
        db,
        statement.order_by(Plant.plant_code, Plant.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/machines", response_model=Page[MachineRead])
def list_machines(
    plant_id: UUID | None = None,
    status: MachineStatus | None = None,
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(Machine)
    count_statement = select(func.count(Machine.id))
    if plant_id is not None:
        statement = statement.where(Machine.plant_id == plant_id)
        count_statement = count_statement.where(Machine.plant_id == plant_id)
    if status is not None:
        statement = statement.where(Machine.status == status)
        count_statement = count_statement.where(Machine.status == status)
    pattern = _search_pattern(search)
    if pattern is not None:
        search_filter = or_(
            Machine.machine_code.ilike(pattern),
            Machine.name.ilike(pattern),
        )
        statement = statement.where(search_filter)
        count_statement = count_statement.where(search_filter)
    return _paginate(
        db,
        statement.order_by(Machine.plant_id, Machine.machine_code, Machine.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/machines/{machine_id}/safety-zones",
    response_model=Page[SafetyZoneRead],
)
def list_machine_safety_zones(
    machine_id: UUID,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(Machine, machine_id) is None:
        raise HTTPException(status_code=404, detail="machine not found")
    statement = (
        select(SafetyZone)
        .join(
            MachineSafetyZone,
            MachineSafetyZone.safety_zone_id == SafetyZone.id,
        )
        .where(MachineSafetyZone.machine_id == machine_id)
    )
    count_statement = (
        select(func.count(MachineSafetyZone.id))
        .where(MachineSafetyZone.machine_id == machine_id)
    )
    return _paginate(
        db,
        statement.order_by(SafetyZone.zone_code, SafetyZone.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/safety-zones", response_model=Page[SafetyZoneRead])
def list_safety_zones(
    plant_id: UUID | None = None,
    status: SafetyZoneStatus | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(SafetyZone)
    count_statement = select(func.count(SafetyZone.id))
    if plant_id is not None:
        statement = statement.where(SafetyZone.plant_id == plant_id)
        count_statement = count_statement.where(SafetyZone.plant_id == plant_id)
    if status is not None:
        statement = statement.where(SafetyZone.status == status)
        count_statement = count_statement.where(SafetyZone.status == status)
    return _paginate(
        db,
        statement.order_by(SafetyZone.plant_id, SafetyZone.zone_code, SafetyZone.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/interlocks", response_model=Page[InterlockRead])
def list_interlocks(
    machine_id: UUID | None = None,
    status: InterlockStatus | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(Interlock)
    count_statement = select(func.count(Interlock.id))
    if machine_id is not None:
        statement = statement.where(Interlock.machine_id == machine_id)
        count_statement = count_statement.where(Interlock.machine_id == machine_id)
    if status is not None:
        statement = statement.where(Interlock.status == status)
        count_statement = count_statement.where(Interlock.status == status)
    return _paginate(
        db,
        statement.order_by(Interlock.machine_id, Interlock.interlock_code, Interlock.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/alarm-definitions", response_model=Page[AlarmDefinitionRead])
def list_alarm_definitions(
    machine_id: UUID | None = None,
    status: AlarmDefinitionStatus | None = None,
    severity: AlarmSeverity | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(AlarmDefinition)
    count_statement = select(func.count(AlarmDefinition.id))
    if machine_id is not None:
        statement = statement.where(AlarmDefinition.machine_id == machine_id)
        count_statement = count_statement.where(
            AlarmDefinition.machine_id == machine_id
        )
    if status is not None:
        statement = statement.where(AlarmDefinition.status == status)
        count_statement = count_statement.where(AlarmDefinition.status == status)
    if severity is not None:
        statement = statement.where(AlarmDefinition.severity == severity)
        count_statement = count_statement.where(AlarmDefinition.severity == severity)
    return _paginate(
        db,
        statement.order_by(
            AlarmDefinition.machine_id,
            AlarmDefinition.alarm_code,
            AlarmDefinition.id,
        ),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/operator-shifts", response_model=Page[OperatorShiftRead])
def list_operator_shifts(
    starts_after: datetime | None = None,
    ends_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(starts_after, ends_before)
    statement = select(OperatorShift)
    count_statement = select(func.count(OperatorShift.id))
    if starts_after is not None:
        statement = statement.where(OperatorShift.starts_at >= starts_after)
        count_statement = count_statement.where(
            OperatorShift.starts_at >= starts_after
        )
    if ends_before is not None:
        statement = statement.where(OperatorShift.ends_at <= ends_before)
        count_statement = count_statement.where(
            OperatorShift.ends_at <= ends_before
        )
    return _paginate(
        db,
        statement.order_by(OperatorShift.starts_at, OperatorShift.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/machines/{machine_id}/state-events",
    response_model=Page[MachineStateEventRead],
)
def list_machine_state_events(
    machine_id: UUID,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(from_at, to_at)
    if db.get(Machine, machine_id) is None:
        raise HTTPException(status_code=404, detail="machine not found")
    statement = select(MachineStateEvent).where(
        MachineStateEvent.machine_id == machine_id
    )
    count_statement = select(func.count(MachineStateEvent.id)).where(
        MachineStateEvent.machine_id == machine_id
    )
    if from_at is not None:
        statement = statement.where(MachineStateEvent.event_at >= from_at)
        count_statement = count_statement.where(
            MachineStateEvent.event_at >= from_at
        )
    if to_at is not None:
        statement = statement.where(MachineStateEvent.event_at < to_at)
        count_statement = count_statement.where(MachineStateEvent.event_at < to_at)
    return _paginate(
        db,
        statement.order_by(MachineStateEvent.event_at, MachineStateEvent.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/alarm-events", response_model=Page[AlarmEventRead])
def list_alarm_events(
    machine_id: UUID | None = None,
    alarm_definition_id: UUID | None = None,
    severity: AlarmSeverity | None = None,
    status: AlarmEventStatus | None = None,
    search: str | None = Query(default=None, max_length=128),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(from_at, to_at)
    statement = select(AlarmEvent)
    count_statement = select(func.count(AlarmEvent.id))
    pattern = _search_pattern(search)
    if severity is not None or pattern is not None:
        statement = statement.join(
            AlarmDefinition,
            AlarmDefinition.id == AlarmEvent.alarm_definition_id,
        )
        count_statement = count_statement.join(
            AlarmDefinition,
            AlarmDefinition.id == AlarmEvent.alarm_definition_id,
        )
    if severity is not None:
        statement = statement.where(AlarmDefinition.severity == severity)
        count_statement = count_statement.where(
            AlarmDefinition.severity == severity
        )
    if pattern is not None:
        search_filter = or_(
            AlarmEvent.external_event_id.ilike(pattern),
            AlarmDefinition.alarm_code.ilike(pattern),
            AlarmDefinition.name.ilike(pattern),
        )
        statement = statement.where(search_filter)
        count_statement = count_statement.where(search_filter)
    if machine_id is not None:
        statement = statement.where(AlarmEvent.machine_id == machine_id)
        count_statement = count_statement.where(AlarmEvent.machine_id == machine_id)
    if alarm_definition_id is not None:
        statement = statement.where(
            AlarmEvent.alarm_definition_id == alarm_definition_id
        )
        count_statement = count_statement.where(
            AlarmEvent.alarm_definition_id == alarm_definition_id
        )
    if status is not None:
        statement = statement.where(AlarmEvent.status == status)
        count_statement = count_statement.where(AlarmEvent.status == status)
    if from_at is not None:
        statement = statement.where(AlarmEvent.event_at >= from_at)
        count_statement = count_statement.where(AlarmEvent.event_at >= from_at)
    if to_at is not None:
        statement = statement.where(AlarmEvent.event_at < to_at)
        count_statement = count_statement.where(AlarmEvent.event_at < to_at)
    return _paginate(
        db,
        statement.order_by(AlarmEvent.event_at, AlarmEvent.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/acknowledgements", response_model=Page[AcknowledgementRead])
def list_acknowledgements(
    alarm_event_id: UUID | None = None,
    operator_shift_id: UUID | None = None,
    status: AcknowledgementStatus | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(from_at, to_at)
    statement = select(Acknowledgement)
    count_statement = select(func.count(Acknowledgement.id))
    for column, value in (
        (Acknowledgement.alarm_event_id, alarm_event_id),
        (Acknowledgement.operator_shift_id, operator_shift_id),
        (Acknowledgement.status, status),
    ):
        if value is not None:
            statement = statement.where(column == value)
            count_statement = count_statement.where(column == value)
    if from_at is not None:
        statement = statement.where(Acknowledgement.acknowledged_at >= from_at)
        count_statement = count_statement.where(
            Acknowledgement.acknowledged_at >= from_at
        )
    if to_at is not None:
        statement = statement.where(Acknowledgement.acknowledged_at < to_at)
        count_statement = count_statement.where(
            Acknowledgement.acknowledged_at < to_at
        )
    return _paginate(
        db,
        statement.order_by(Acknowledgement.acknowledged_at, Acknowledgement.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/incidents", response_model=Page[IncidentRead])
def list_incidents(
    machine_id: UUID | None = None,
    status: IncidentStatus | None = None,
    search: str | None = Query(default=None, max_length=128),
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(from_at, to_at)
    statement = select(Incident)
    count_statement = select(func.count(Incident.id))
    for column, value in (
        (Incident.machine_id, machine_id),
        (Incident.status, status),
    ):
        if value is not None:
            statement = statement.where(column == value)
            count_statement = count_statement.where(column == value)
    pattern = _search_pattern(search)
    if pattern is not None:
        statement = statement.join(Machine, Machine.id == Incident.machine_id)
        count_statement = count_statement.join(
            Machine, Machine.id == Incident.machine_id
        )
        search_filter = or_(
            Incident.business_id.ilike(pattern),
            Incident.issue_type.ilike(pattern),
            Machine.machine_code.ilike(pattern),
        )
        statement = statement.where(search_filter)
        count_statement = count_statement.where(search_filter)
    if from_at is not None:
        statement = statement.where(Incident.opened_at >= from_at)
        count_statement = count_statement.where(Incident.opened_at >= from_at)
    if to_at is not None:
        statement = statement.where(Incident.opened_at < to_at)
        count_statement = count_statement.where(Incident.opened_at < to_at)
    return _paginate(
        db,
        statement.order_by(Incident.opened_at, Incident.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/incidents/{incident_id}/evidence",
    response_model=IncidentEvidence,
)
def get_incident_evidence(
    incident_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    alarm_events = db.scalars(
        select(AlarmEvent)
        .join(
            IncidentAlarmEvent,
            IncidentAlarmEvent.alarm_event_id == AlarmEvent.id,
        )
        .where(IncidentAlarmEvent.incident_id == incident.id)
        .order_by(AlarmEvent.event_at, AlarmEvent.id)
    ).all()
    incident_history = db.scalars(
        select(LifecycleHistory)
        .where(
            LifecycleHistory.entity_type == LifecycleEntityType.INCIDENT,
            LifecycleHistory.entity_id == incident.id,
        )
        .order_by(LifecycleHistory.occurred_at, LifecycleHistory.id)
    ).all()
    state_event_ids: set[UUID] = set()
    for history_item in incident_history:
        payload = history_item.payload
        if not isinstance(payload, dict):
            continue
        direct_id = payload.get("machine_state_event_id")
        if direct_id:
            state_event_ids.add(UUID(str(direct_id)))
        for context in payload.get("linked_contexts", []):
            state_id = context.get("machine_state_event_id")
            if state_id:
                state_event_ids.add(UUID(str(state_id)))
    machine_state_events = (
        db.scalars(
            select(MachineStateEvent)
            .where(MachineStateEvent.id.in_(state_event_ids))
            .order_by(MachineStateEvent.event_at, MachineStateEvent.id)
        ).all()
        if state_event_ids
        else []
    )
    corrective_actions = db.scalars(
        select(CorrectiveAction)
        .where(CorrectiveAction.incident_id == incident.id)
        .order_by(CorrectiveAction.due_at, CorrectiveAction.id)
    ).all()
    action_ids = [action.id for action in corrective_actions]
    action_history = (
        db.scalars(
            select(LifecycleHistory)
            .where(
                LifecycleHistory.entity_type
                == LifecycleEntityType.CORRECTIVE_ACTION,
                LifecycleHistory.entity_id.in_(action_ids),
            )
            .order_by(LifecycleHistory.occurred_at, LifecycleHistory.id)
        ).all()
        if action_ids
        else []
    )
    lifecycle_history = sorted(
        incident_history + action_history,
        key=lambda history: (history.occurred_at, history.id),
    )
    return {
        "incident": incident,
        "alarm_events": alarm_events,
        "machine_state_events": machine_state_events,
        "corrective_actions": corrective_actions,
        "lifecycle_history": lifecycle_history,
        "traceability": analytics.incident_traceability(
            incident,
            alarm_events,
            machine_state_events,
            corrective_actions,
            lifecycle_history,
        ),
    }


@router.get("/corrective-actions", response_model=Page[CorrectiveActionRead])
def list_corrective_actions(
    incident_id: UUID | None = None,
    status: CorrectiveActionStatus | None = None,
    search: str | None = Query(default=None, max_length=128),
    due_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    statement = select(CorrectiveAction)
    count_statement = select(func.count(CorrectiveAction.id))
    for column, value in (
        (CorrectiveAction.incident_id, incident_id),
        (CorrectiveAction.status, status),
    ):
        if value is not None:
            statement = statement.where(column == value)
            count_statement = count_statement.where(column == value)
    pattern = _search_pattern(search)
    if pattern is not None:
        search_filter = or_(
            CorrectiveAction.business_id.ilike(pattern),
            CorrectiveAction.owner.ilike(pattern),
        )
        statement = statement.where(search_filter)
        count_statement = count_statement.where(search_filter)
    if due_before is not None:
        statement = statement.where(CorrectiveAction.due_at <= due_before)
        count_statement = count_statement.where(
            CorrectiveAction.due_at <= due_before
        )
    return _paginate(
        db,
        statement.order_by(CorrectiveAction.due_at, CorrectiveAction.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/lifecycle-history", response_model=Page[LifecycleHistoryRead])
def list_lifecycle_history(
    entity_type: LifecycleEntityType | None = None,
    entity_id: UUID | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(from_at, to_at)
    statement = select(LifecycleHistory)
    count_statement = select(func.count(LifecycleHistory.id))
    for column, value in (
        (LifecycleHistory.entity_type, entity_type),
        (LifecycleHistory.entity_id, entity_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
            count_statement = count_statement.where(column == value)
    if from_at is not None:
        statement = statement.where(LifecycleHistory.occurred_at >= from_at)
        count_statement = count_statement.where(
            LifecycleHistory.occurred_at >= from_at
        )
    if to_at is not None:
        statement = statement.where(LifecycleHistory.occurred_at < to_at)
        count_statement = count_statement.where(
            LifecycleHistory.occurred_at < to_at
        )
    return _paginate(
        db,
        statement.order_by(LifecycleHistory.occurred_at, LifecycleHistory.id),
        count_statement,
        limit=limit,
        offset=offset,
    )


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def get_analytics_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return analytics.get_summary(db)


@router.get(
    "/analytics/alarm-clusters",
    response_model=Page[AlarmClusterRead],
)
def list_alarm_clusters(
    machine_id: UUID | None = None,
    severity: AlarmSeverity | None = None,
    kind: Literal["nuisance", "severe"] | None = None,
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return analytics.list_alarm_clusters(
        db,
        machine_id=machine_id,
        severity=severity.value if severity is not None else None,
        kind=kind,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/alarm-events", response_model=AlarmIngestionResult)
def ingest_alarm(
    payload: AlarmEventCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return safety_workflow.ingest_alarm_event(db, payload)


@router.post(
    "/machine-state-events",
    response_model=MachineStateIngestionResult,
)
def ingest_machine_state(
    payload: MachineStateEventCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return safety_workflow.ingest_machine_state_event(db, payload)


@router.post("/acknowledgements", response_model=AcknowledgementResult)
def create_acknowledgement(
    payload: AcknowledgementCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return safety_workflow.record_acknowledgement(db, payload)


@router.post("/incidents", response_model=IncidentResult)
def create_incident(
    payload: IncidentCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = safety_workflow.create_incident(db, payload)
    return result


@router.patch("/incidents/{incident_id}/status", response_model=IncidentRead)
def update_incident_status(
    incident_id: UUID,
    payload: IncidentTransitionRequest,
    db: Session = Depends(get_db),
) -> Incident:
    return safety_workflow.transition_incident(db, incident_id, payload)["incident"]


@router.post(
    "/corrective-actions",
    response_model=CorrectiveActionResult,
)
def create_action(
    payload: CorrectiveActionCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return safety_workflow.create_corrective_action(db, payload)


@router.patch(
    "/corrective-actions/{action_id}/status",
    response_model=CorrectiveActionRead,
)
def update_action_status(
    action_id: UUID,
    payload: CorrectiveActionTransitionRequest,
    db: Session = Depends(get_db),
) -> CorrectiveAction:
    return safety_workflow.transition_corrective_action(db, action_id, payload)["action"]


@router.post(
    "/corrective-actions/{action_id}/verify",
    response_model=CorrectiveActionResult,
)
def verify_action(
    action_id: UUID,
    payload: CorrectiveActionVerificationRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return safety_workflow.verify_corrective_action(db, action_id, payload)
