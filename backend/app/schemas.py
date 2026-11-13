from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    AcknowledgementStatus,
    AlarmDefinitionStatus,
    AlarmEventStatus,
    AlarmSeverity,
    CorrectiveActionStatus,
    IncidentStatus,
    InterlockState,
    InterlockStatus,
    LifecycleEntityType,
    MachineState,
    MachineStatus,
    PlantStatus,
    SafetyZoneStatus,
    VerificationResult,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PlantRead(ORMModel):
    id: UUID
    plant_code: str
    name: str
    status: PlantStatus
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class MachineRead(ORMModel):
    id: UUID
    plant_id: UUID
    machine_code: str
    name: str
    status: MachineStatus
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class SafetyZoneRead(ORMModel):
    id: UUID
    plant_id: UUID
    business_id: str
    zone_code: str
    name: str
    status: SafetyZoneStatus
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class InterlockRead(ORMModel):
    id: UUID
    machine_id: UUID
    interlock_code: str
    description: str
    required_state: InterlockState
    status: InterlockStatus
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class AlarmDefinitionRead(ORMModel):
    id: UUID
    machine_id: UUID
    business_id: str
    alarm_code: str
    name: str
    severity: AlarmSeverity
    acknowledgement_required: bool
    ack_sla_seconds: int
    status: AlarmDefinitionStatus
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class OperatorShiftRead(ORMModel):
    id: UUID
    shift_code: str
    operator_code: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime


class AlarmEventRead(ORMModel):
    id: UUID
    machine_id: UUID
    alarm_definition_id: UUID
    external_event_id: str
    event_at: datetime
    received_at: datetime
    status: AlarmEventStatus
    cleared_at: datetime | None
    issue_type: str | None
    created_at: datetime


class MachineStateEventRead(ORMModel):
    id: UUID
    machine_id: UUID
    interlock_id: UUID | None
    state: MachineState
    interlock_state: InterlockState | None
    event_at: datetime
    source_event_id: str
    issue_type: str | None
    created_at: datetime


class AcknowledgementRead(ORMModel):
    id: UUID
    business_id: str
    alarm_event_id: UUID
    operator_shift_id: UUID
    acknowledged_at: datetime
    status: AcknowledgementStatus
    issue_type: str | None
    created_at: datetime


class IncidentRead(ORMModel):
    id: UUID
    business_id: str
    machine_id: UUID
    opened_at: datetime
    status: IncidentStatus
    issue_type: str
    created_at: datetime
    updated_at: datetime


class CorrectiveActionRead(ORMModel):
    id: UUID
    business_id: str
    incident_id: UUID
    owner: str
    due_at: datetime
    status: CorrectiveActionStatus
    completed_at: datetime | None
    verification_result: VerificationResult
    verification_evidence: str | None
    created_at: datetime
    updated_at: datetime


class LifecycleHistoryRead(ORMModel):
    id: UUID
    entity_type: LifecycleEntityType
    entity_id: UUID
    transition: str
    occurred_at: datetime
    actor_reference: str | None
    reason: str | None
    payload: dict[str, object]
    created_at: datetime


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class AlarmEventCreate(BaseModel):
    machine_id: UUID
    alarm_definition_id: UUID
    external_event_id: str = Field(min_length=1, max_length=160)
    event_at: datetime
    received_at: datetime

    _aware_timestamps = field_validator("event_at", "received_at")(_require_timezone)


class MachineStateEventCreate(BaseModel):
    machine_id: UUID
    interlock_id: UUID | None = None
    state: MachineState
    interlock_state: InterlockState | None = None
    event_at: datetime
    source_event_id: str = Field(min_length=1, max_length=160)
    actor_reference: str | None = Field(default="event_ingestion", max_length=160)

    _aware_event_at = field_validator("event_at")(_require_timezone)


class AcknowledgementCreate(BaseModel):
    business_id: str = Field(min_length=1, max_length=192)
    alarm_event_id: UUID
    operator_shift_id: UUID
    acknowledged_at: datetime
    actor_reference: str | None = Field(default="operator", max_length=160)

    _aware_acknowledged_at = field_validator("acknowledged_at")(_require_timezone)


class IncidentCreate(BaseModel):
    business_id: str = Field(min_length=1, max_length=64)
    machine_id: UUID
    alarm_event_ids: list[UUID] = Field(min_length=1, max_length=100)
    opened_at: datetime
    issue_type: Literal["unsafe_condition"] = "unsafe_condition"
    actor_reference: str | None = Field(default="safety_rule", max_length=160)

    _aware_opened_at = field_validator("opened_at")(_require_timezone)

    @field_validator("alarm_event_ids")
    @classmethod
    def unique_alarm_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("alarm_event_ids must not contain duplicates")
        return values


class IncidentTransitionRequest(BaseModel):
    status: IncidentStatus
    occurred_at: datetime
    actor_reference: str | None = Field(default="safety_workflow", max_length=160)
    reason: str | None = Field(default=None, max_length=2000)

    _aware_occurred_at = field_validator("occurred_at")(_require_timezone)


class CorrectiveActionCreate(BaseModel):
    business_id: str = Field(min_length=1, max_length=192)
    incident_id: UUID
    owner: str = Field(min_length=1, max_length=128)
    due_at: datetime
    created_at: datetime
    actor_reference: str | None = Field(default="maintenance_workflow", max_length=160)

    _aware_timestamps = field_validator("due_at", "created_at")(_require_timezone)

    @model_validator(mode="after")
    def validate_due_date(self) -> CorrectiveActionCreate:
        if self.due_at <= self.created_at:
            raise ValueError("due_at must be later than created_at")
        return self


class CorrectiveActionTransitionRequest(BaseModel):
    status: CorrectiveActionStatus
    occurred_at: datetime
    actor_reference: str | None = Field(default="maintenance_workflow", max_length=160)
    reason: str | None = Field(default=None, max_length=2000)

    _aware_occurred_at = field_validator("occurred_at")(_require_timezone)


class CorrectiveActionVerificationRequest(BaseModel):
    result: Literal[VerificationResult.PASSED, VerificationResult.FAILED]
    evidence: str = Field(min_length=1, max_length=10000)
    verified_at: datetime
    actor_reference: str | None = Field(default="quality_verification", max_length=160)

    _aware_verified_at = field_validator("verified_at")(_require_timezone)


class AlarmDecision(BaseModel):
    severity: AlarmSeverity
    acknowledgement_required: bool
    ack_sla_seconds: int
    unsafe_context: bool
    incident_created: bool
    incident_id: UUID | None
    exceptions: list[str]


class AlarmIngestionResult(BaseModel):
    event: AlarmEventRead
    idempotent: bool
    decision: AlarmDecision


class MachineStateIngestionResult(BaseModel):
    event: MachineStateEventRead
    idempotent: bool
    decision: str
    exceptions: list[str]


class AcknowledgementResult(BaseModel):
    acknowledgement: AcknowledgementRead
    idempotent: bool
    sla_seconds: int
    late_by_seconds: int


class IncidentResult(BaseModel):
    incident: IncidentRead
    idempotent: bool
    linked_alarm_event_ids: list[UUID]


class IncidentTraceability(BaseModel):
    traceable: bool
    source_alarm_event_count: int
    machine_state_event_count: int
    corrective_action_count: int
    lifecycle_record_count: int
    action_lifecycle_record_count: int
    closed_unverified_action_count: int
    exceptions: list[str]


class IncidentTraceabilitySummary(BaseModel):
    incident_count: int
    traceable_incident_count: int
    traceability_exception_incident_count: int
    missing_alarm_context_incident_count: int
    missing_machine_state_context_incident_count: int
    missing_lifecycle_history_incident_count: int
    missing_action_lifecycle_history_incident_count: int
    closed_unverified_action_incident_count: int


class IncidentEvidence(BaseModel):
    incident: IncidentRead
    alarm_events: list[AlarmEventRead]
    machine_state_events: list[MachineStateEventRead]
    corrective_actions: list[CorrectiveActionRead]
    lifecycle_history: list[LifecycleHistoryRead]
    traceability: IncidentTraceability


class CorrectiveActionResult(BaseModel):
    action: CorrectiveActionRead
    idempotent: bool


class AlarmClusterRead(BaseModel):
    machine_id: UUID
    machine_code: str
    alarm_definition_id: UUID
    alarm_code: str
    alarm_name: str
    severity: AlarmSeverity
    event_count: int
    starts_at: datetime
    ends_at: datetime


class AnalyticsSummary(BaseModel):
    alarm_event_count: int
    alarm_events_by_severity: dict[str, int]
    severe_alarm_event_count: int
    nuisance_alarm_event_count: int
    acknowledgement_count: int
    timely_acknowledgement_count: int
    late_acknowledgement_count: int
    excused_acknowledgement_count: int
    acknowledgement_before_alarm_count: int
    unacknowledged_required_alarm_event_count: int
    acknowledgement_sla_exception_count: int
    mean_acknowledgement_response_seconds: float | None
    acknowledgement_response_observation_count: int
    incident_count_by_status: dict[str, int]
    corrective_action_count_by_status: dict[str, int]
    verified_corrective_action_count: int
    closed_unverified_corrective_action_count: int
    repeated_alarm_cluster_count: int
    alarm_cluster_count_by_kind: dict[str, int]
    incident_traceability: IncidentTraceabilitySummary
