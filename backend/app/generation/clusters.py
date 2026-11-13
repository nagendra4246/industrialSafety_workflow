from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from .dataset import Record


ALARM_CLUSTER_WINDOW = timedelta(minutes=1)
MINIMUM_ALARM_CLUSTER_SIZE = 3


def find_alarm_clusters(
    alarm_events: Iterable[Record],
) -> list[list[Record]]:
    grouped: dict[tuple[object, object], list[Record]] = {}
    for event in alarm_events:
        key = (event["machine_id"], event["alarm_definition_id"])
        grouped.setdefault(key, []).append(event)

    clusters: list[list[Record]] = []
    for events in grouped.values():
        events.sort(key=lambda row: (row["event_at"], str(row["id"])))
        current: list[Record] = []
        for event in events:
            if (
                current
                and event["event_at"] - current[-1]["event_at"]
                > ALARM_CLUSTER_WINDOW
            ):
                if len(current) >= MINIMUM_ALARM_CLUSTER_SIZE:
                    clusters.append(current)
                current = []
            current.append(event)
        if len(current) >= MINIMUM_ALARM_CLUSTER_SIZE:
            clusters.append(current)

    clusters.sort(
        key=lambda rows: (
            rows[0]["event_at"],
            str(rows[0]["machine_id"]),
            str(rows[0]["alarm_definition_id"]),
        )
    )
    return clusters
