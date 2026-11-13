from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


Record = dict[str, Any]

TABLE_ORDER = (
    "plant",
    "machine",
    "safety_zone",
    "machine_safety_zone",
    "interlock",
    "alarm_definition",
    "operator_shift",
    "machine_state_event",
    "alarm_event",
    "acknowledgement",
    "incident",
    "incident_alarm_event",
    "corrective_action",
    "lifecycle_history",
)


@dataclass(frozen=True)
class GenerationConfig:
    seed: int = 20261022
    plant_count: int = 4
    machine_count: int = 60
    alarm_definition_count: int = 200
    alarm_event_count: int = 50_000
    days: int = 30
    start_at: datetime = datetime(2026, 9, 23, tzinfo=timezone.utc)

    def __post_init__(self) -> None:
        for field_name in (
            "plant_count",
            "machine_count",
            "alarm_definition_count",
            "days",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        if self.alarm_event_count < 10:
            raise ValueError("alarm_event_count must be at least 10 to generate all edge cases")
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at must include a timezone")
        object.__setattr__(self, "start_at", self.start_at.astimezone(timezone.utc))

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(days=self.days)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "plant_count": self.plant_count,
            "machine_count": self.machine_count,
            "alarm_definition_count": self.alarm_definition_count,
            "alarm_event_count": self.alarm_event_count,
            "days": self.days,
            "start_at": self.start_at.isoformat(),
            "end_at_exclusive": self.end_at.isoformat(),
        }


@dataclass
class SyntheticDataset:
    config: GenerationConfig
    tables: dict[str, list[Record]] = field(
        default_factory=lambda: {name: [] for name in TABLE_ORDER}
    )
    replay_deliveries: list[Record] = field(default_factory=list)
    ground_truth: list[Record] = field(default_factory=list)

    def row_counts(self) -> dict[str, int]:
        return {name: len(self.tables[name]) for name in TABLE_ORDER}


def default_export_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "exports"
