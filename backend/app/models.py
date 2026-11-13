from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine


class Base(DeclarativeBase):
    pass


def enum_type(enum_class: type[enum.Enum], name: str) -> TypeEngine[Any]:
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
    )


json_document = JSON().with_variant(JSONB(), "postgresql")


class PlantStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class MachineStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    DECOMMISSIONED = "decommissioned"


class SafetyZoneStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class InterlockStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class InterlockState(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    ENERGIZED = "energized"
    DEENERGIZED = "deenergized"


class AlarmSeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlarmDefinitionStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class AlarmEventStatus(str, enum.Enum):
    ACTIVE = "active"
    CLEARED = "cleared"


class MachineState(str, enum.Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAULT = "fault"
    INTERLOCK_ENGAGED = "interlock_engaged"
    INTERLOCK_BYPASSED = "interlock_bypassed"
    MAINTENANCE = "maintenance"


class AcknowledgementStatus(str, enum.Enum):
    ACKNOWLEDGED = "acknowledged"
    LATE = "late"
    EXCUSED = "excused"


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class CorrectiveActionStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class VerificationResult(str, enum.Enum):
    NOT_STARTED = "not_started"
    PASSED = "passed"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"


class LifecycleEntityType(str, enum.Enum):
    PLANT = "plant"
    MACHINE = "machine"
    SAFETY_ZONE = "safety_zone"
    INTERLOCK = "interlock"
    ALARM_DEFINITION = "alarm_definition"
    ALARM_EVENT = "alarm_event"
    OPERATOR_SHIFT = "operator_shift"
    ACKNOWLEDGEMENT = "acknowledgement"
    INCIDENT = "incident"
    CORRECTIVE_ACTION = "corrective_action"
    MACHINE_STATE_EVENT = "machine_state_event"


class Plant(Base):
    __tablename__ = "plant"
    __table_args__ = (
        UniqueConstraint("plant_code", name="uq_plant_plant_code"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_plant_effective_dates",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[PlantStatus] = mapped_column(
        enum_type(PlantStatus, "plant_status"),
        nullable=False,
        default=PlantStatus.ACTIVE,
        server_default=PlantStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Machine(Base):
    __tablename__ = "machine"
    __table_args__ = (
        UniqueConstraint("plant_id", "machine_code", name="uq_machine_plant_machine_code"),
        UniqueConstraint("id", "plant_id", name="uq_machine_id_plant_id"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_machine_effective_dates",
        ),
        Index("ix_machine_plant_id", "plant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plant.id"), nullable=False
    )
    machine_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[MachineStatus] = mapped_column(
        enum_type(MachineStatus, "machine_status"),
        nullable=False,
        default=MachineStatus.ACTIVE,
        server_default=MachineStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SafetyZone(Base):
    __tablename__ = "safety_zone"
    __table_args__ = (
        UniqueConstraint("plant_id", "zone_code", name="uq_safety_zone_plant_zone_code"),
        UniqueConstraint("id", "plant_id", name="uq_safety_zone_id_plant_id"),
        UniqueConstraint("business_id", name="uq_safety_zone_business_id"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_safety_zone_effective_dates",
        ),
        Index("ix_safety_zone_plant_id", "plant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("plant.id"), nullable=False
    )
    business_id: Mapped[str] = mapped_column(String(96), nullable=False)
    zone_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[SafetyZoneStatus] = mapped_column(
        enum_type(SafetyZoneStatus, "safety_zone_status"),
        nullable=False,
        default=SafetyZoneStatus.ACTIVE,
        server_default=SafetyZoneStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MachineSafetyZone(Base):
    __tablename__ = "machine_safety_zone"
    __table_args__ = (
        ForeignKeyConstraint(
            ["machine_id", "plant_id"],
            ["machine.id", "machine.plant_id"],
            name="fk_machine_safety_zone_machine_plant",
        ),
        ForeignKeyConstraint(
            ["safety_zone_id", "plant_id"],
            ["safety_zone.id", "safety_zone.plant_id"],
            name="fk_machine_safety_zone_zone_plant",
        ),
        UniqueConstraint(
            "machine_id", "safety_zone_id", "effective_from",
            name="uq_machine_safety_zone_effective_from",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_machine_safety_zone_effective_dates",
        ),
        Index("ix_machine_safety_zone_safety_zone_id", "safety_zone_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    safety_zone_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interlock(Base):
    __tablename__ = "interlock"
    __table_args__ = (
        UniqueConstraint("machine_id", "interlock_code", name="uq_interlock_machine_code"),
        UniqueConstraint("id", "machine_id", name="uq_interlock_id_machine_id"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_interlock_effective_dates",
        ),
        Index("ix_interlock_machine_id", "machine_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machine.id"), nullable=False
    )
    interlock_code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_state: Mapped[InterlockState] = mapped_column(
        enum_type(InterlockState, "interlock_state"), nullable=False
    )
    status: Mapped[InterlockStatus] = mapped_column(
        enum_type(InterlockStatus, "interlock_status"),
        nullable=False,
        default=InterlockStatus.ACTIVE,
        server_default=InterlockStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AlarmDefinition(Base):
    __tablename__ = "alarm_definition"
    __table_args__ = (
        UniqueConstraint("machine_id", "alarm_code", name="uq_alarm_definition_machine_code"),
        UniqueConstraint("id", "machine_id", name="uq_alarm_definition_id_machine_id"),
        UniqueConstraint("business_id", name="uq_alarm_definition_business_id"),
        CheckConstraint("ack_sla_seconds >= 0", name="ck_alarm_definition_ack_sla_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_alarm_definition_effective_dates",
        ),
        Index("ix_alarm_definition_machine_id", "machine_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machine.id"), nullable=False
    )
    business_id: Mapped[str] = mapped_column(String(128), nullable=False)
    alarm_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[AlarmSeverity] = mapped_column(
        enum_type(AlarmSeverity, "alarm_severity"), nullable=False
    )
    acknowledgement_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ack_sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AlarmDefinitionStatus] = mapped_column(
        enum_type(AlarmDefinitionStatus, "alarm_definition_status"),
        nullable=False,
        default=AlarmDefinitionStatus.ACTIVE,
        server_default=AlarmDefinitionStatus.ACTIVE.value,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AlarmEvent(Base):
    __tablename__ = "alarm_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["alarm_definition_id", "machine_id"],
            ["alarm_definition.id", "alarm_definition.machine_id"],
            name="fk_alarm_event_definition_machine",
        ),
        UniqueConstraint("external_event_id", name="uq_alarm_event_external_event_id"),
        CheckConstraint(
            "cleared_at IS NULL OR cleared_at >= event_at",
            name="ck_alarm_event_clear_order",
        ),
        Index("ix_alarm_event_machine_event_at", "machine_id", "event_at"),
        Index(
            "ix_alarm_event_machine_definition_time",
            "machine_id",
            "alarm_definition_id",
            "event_at",
            "id",
        ),
        Index("ix_alarm_event_event_at_id", "event_at", "id"),
        Index("ix_alarm_event_alarm_definition_id", "alarm_definition_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machine.id"), nullable=False
    )
    alarm_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AlarmEventStatus] = mapped_column(
        enum_type(AlarmEventStatus, "alarm_event_status"),
        nullable=False,
        default=AlarmEventStatus.ACTIVE,
        server_default=AlarmEventStatus.ACTIVE.value,
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issue_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperatorShift(Base):
    __tablename__ = "operator_shift"
    __table_args__ = (
        UniqueConstraint("shift_code", name="uq_operator_shift_shift_code"),
        CheckConstraint("ends_at > starts_at", name="ck_operator_shift_time_order"),
        Index("ix_operator_shift_starts_at_ends_at", "starts_at", "ends_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_code: Mapped[str] = mapped_column(String(96), nullable=False)
    operator_code: Mapped[str] = mapped_column(String(96), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Acknowledgement(Base):
    __tablename__ = "acknowledgement"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_acknowledgement_business_id"),
        Index("ix_acknowledgement_alarm_id", "alarm_event_id"),
        Index("ix_acknowledgement_acknowledged_at_id", "acknowledged_at", "id"),
        Index("ix_acknowledgement_shift_id", "operator_shift_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[str] = mapped_column(String(192), nullable=False)
    alarm_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alarm_event.id"), nullable=False
    )
    operator_shift_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("operator_shift.id"), nullable=False
    )
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AcknowledgementStatus] = mapped_column(
        enum_type(AcknowledgementStatus, "acknowledgement_status"), nullable=False
    )
    issue_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Incident(Base):
    __tablename__ = "incident"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_incident_business_id"),
        Index("ix_incident_machine_opened_at", "machine_id", "opened_at"),
        Index("ix_incident_opened_at_id", "opened_at", "id"),
        Index("ix_incident_status_opened_at_id", "status", "opened_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machine.id"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        enum_type(IncidentStatus, "incident_status"),
        nullable=False,
        default=IncidentStatus.OPEN,
        server_default=IncidentStatus.OPEN.value,
    )
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class IncidentAlarmEvent(Base):
    __tablename__ = "incident_alarm_event"
    __table_args__ = (
        UniqueConstraint("incident_id", "alarm_event_id", name="uq_incident_alarm_event_pair"),
        Index("ix_incident_alarm_event_alarm_event_id", "alarm_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incident.id"), nullable=False
    )
    alarm_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("alarm_event.id"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CorrectiveAction(Base):
    __tablename__ = "corrective_action"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_corrective_action_business_id"),
        Index("ix_corrective_action_incident_id", "incident_id"),
        Index("ix_corrective_action_due_at_id", "due_at", "id"),
        Index("ix_corrective_action_status_due_date", "status", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[str] = mapped_column(String(192), nullable=False)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incident.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[CorrectiveActionStatus] = mapped_column(
        enum_type(CorrectiveActionStatus, "corrective_action_status"),
        nullable=False,
        default=CorrectiveActionStatus.OPEN,
        server_default=CorrectiveActionStatus.OPEN.value,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_result: Mapped[VerificationResult] = mapped_column(
        enum_type(VerificationResult, "verification_result"),
        nullable=False,
        default=VerificationResult.NOT_STARTED,
        server_default=VerificationResult.NOT_STARTED.value,
    )
    verification_evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MachineStateEvent(Base):
    __tablename__ = "machine_state_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interlock_id", "machine_id"],
            ["interlock.id", "interlock.machine_id"],
            name="fk_machine_state_event_interlock_machine",
        ),
        UniqueConstraint("source_event_id", name="uq_machine_state_event_source_event_id"),
        Index("ix_machine_state_event_machine_event_at", "machine_id", "event_at"),
        Index("ix_machine_state_event_event_at_id", "event_at", "id"),
        Index("ix_machine_state_event_interlock_id", "interlock_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("machine.id"), nullable=False
    )
    interlock_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    state: Mapped[MachineState] = mapped_column(
        enum_type(MachineState, "machine_state"), nullable=False
    )
    interlock_state: Mapped[InterlockState | None] = mapped_column(
        enum_type(InterlockState, "machine_state_interlock_state")
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    issue_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LifecycleHistory(Base):
    __tablename__ = "lifecycle_history"
    __table_args__ = (
        Index("ix_lifecycle_history_entity", "entity_type", "entity_id", "occurred_at"),
        Index("ix_lifecycle_history_occurred_at_id", "occurred_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[LifecycleEntityType] = mapped_column(
        enum_type(LifecycleEntityType, "lifecycle_entity_type"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    transition: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_reference: Mapped[str | None] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(json_document, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
