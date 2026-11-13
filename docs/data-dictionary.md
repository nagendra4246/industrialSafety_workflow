# Industrial Safety Workflow schema and data dictionary

## Conventions

- PostgreSQL stores internal primary keys as `UUID`; application-generated UUIDs are immutable.
- All timestamps are `TIMESTAMPTZ` and represent UTC instants. Date intervals are half-open in application queries: `effective_from <= t < effective_to`, with a null end meaning open-ended.
- `NOT NULL` means required. `NULL` means optional. Database defaults are shown in the validation column.
- Enumeration values are lowercase strings, enforced by check constraints generated from the model enums.
- Business identifiers are immutable. Codes explicitly marked scoped are unique only with their parent key; do not assume globally unique short codes.
- `MachineStateEvent`, `AlarmEvent`, and `LifecycleHistory` are append-only at the database level. Business IDs and scoped codes are immutable after insert.
- Chronology that is intentionally allowed to contain labelled anomalies (for example acknowledgement before alarm, or corrective action closed without verification) is not rejected by a database check. It must remain traceable for later validation.

## Relationship cardinality and optionality

| Parent and child | Cardinality | Optionality and enforcement |
|---|---|---|
| Plant → Machine | 1:N | A plant may have zero or more machines; each machine requires one plant (`machine.plant_id`). |
| Plant → SafetyZone | 1:N | A plant may have zero or more zones; each zone requires one plant (`safety_zone.plant_id`). |
| Machine ↔ SafetyZone | N:M | Both sides may have zero or more links. `machine_safety_zone` is the bridge; its composite foreign keys require both rows to belong to the same plant. Effective intervals are recorded on each link. |
| Machine → Interlock | 1:N | A machine may have zero or more interlocks; each interlock requires one machine. |
| Machine → AlarmDefinition | 1:N | A machine may have zero or more definitions; each definition requires one machine. Alarm code is unique within its machine. |
| AlarmDefinition → AlarmEvent | 1:N | A definition may have zero or more events; each event requires exactly one definition. A composite FK also guarantees the event machine matches the definition machine. |
| Machine → AlarmEvent | 1:N | A machine may have zero or more events; each event requires one machine. |
| Machine → MachineStateEvent | 1:N | A machine may have zero or more immutable state events; each event requires one machine. An event may optionally refer to an interlock on that same machine. |
| AlarmEvent → Acknowledgement | 1:N | An alarm may have zero or more acknowledgements; every acknowledgement requires exactly one alarm. |
| OperatorShift → Acknowledgement | 1:N | A shift may have zero or more acknowledgements; every acknowledgement requires exactly one shift. |
| Machine → Incident | 1:N | A machine may have zero or more incidents; every incident requires one machine. |
| Incident ↔ AlarmEvent | N:M | Either side may have zero or more links. `incident_alarm_event` is the bridge; a pair cannot be linked twice. |
| Incident → CorrectiveAction | 1:N | An incident may have zero or more actions; every action requires one incident. |
| Any tracked entity → LifecycleHistory | 1:N | An entity may have zero or more lifecycle records. `entity_type` and `entity_id` form a polymorphic reference, so no single SQL FK can target all entity tables. History is append-only. |

No 1:1 domain relationship is required by the supplied workflow.

## Core entities

### Plant

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `plant_code` | VARCHAR(64) | Yes | Plant code | Global unique | Immutable after insert. |
| `name` | VARCHAR(160) | Yes | Name text | — | Required (`NOT NULL`). |
| `status` | VARCHAR(32) | Yes | `active`, `inactive` | — | Enum check; defaults to `active`. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### Machine

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `plant_id` | UUID | Yes | Existing `Plant.id` | — | FK to `plant.id`; restricted from orphaning. |
| `machine_code` | VARCHAR(64) | Yes | Plant-local machine code | `(plant_id, machine_code)` | Immutable; repeated across different plants is allowed. |
| `name` | VARCHAR(160) | Yes | Name text | — | Required (`NOT NULL`). |
| `status` | VARCHAR(32) | Yes | `active`, `inactive`, `maintenance`, `decommissioned` | — | Enum check; defaults to `active`. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### SafetyZone

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `plant_id` | UUID | Yes | Existing `Plant.id` | — | FK to `plant.id`. |
| `business_id` | VARCHAR(96) | Yes | `SAFE-<plant-id>-<3-digit-counter>`; `<plant-id>` is the immutable `Plant.plant_code` | Global unique | Immutable; generated consistently from the referenced plant and sequence. |
| `zone_code` | VARCHAR(64) | Yes | Plant-local zone code | `(plant_id, zone_code)` | Immutable; repeats across plants are allowed. |
| `name` | VARCHAR(160) | Yes | Name text | — | Required (`NOT NULL`). |
| `status` | VARCHAR(32) | Yes | `active`, `inactive` | — | Enum check; defaults to `active`. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### Interlock

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | — | FK to `machine.id`. |
| `interlock_code` | VARCHAR(64) | Yes | Machine-local interlock code | `(machine_id, interlock_code)` | Immutable; repeated across machines is allowed. |
| `description` | TEXT | Yes | Description | — | Required. |
| `required_state` | VARCHAR(32) | Yes | `open`, `closed`, `energized`, `deenergized` | — | Enum check. |
| `status` | VARCHAR(32) | Yes | `active`, `inactive` | — | Enum check; defaults to `active`. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### AlarmDefinition

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | — | FK to `machine.id`. |
| `business_id` | VARCHAR(128) | Yes | `ALM-<machine-id>-<6-digit-counter>`; `<machine-id>` is the immutable `Machine.machine_code` | Global unique | Immutable; generated consistently for its machine. |
| `alarm_code` | VARCHAR(64) | Yes | Machine-local alarm code | `(machine_id, alarm_code)` | Immutable; repeated across machines is allowed. |
| `name` | VARCHAR(160) | Yes | Name text | — | Required (`NOT NULL`). |
| `severity` | VARCHAR(32) | Yes | `info`, `low`, `medium`, `high`, `critical` | — | Enum check. |
| `acknowledgement_required` | BOOLEAN | Yes | Boolean | — | Defaults to true. |
| `ack_sla_seconds` | INTEGER | Yes | Seconds, non-negative | — | Check `>= 0`. |
| `status` | VARCHAR(32) | Yes | `active`, `inactive` | — | Enum check; defaults to `active`. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### AlarmEvent

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | — | FK to `machine.id`; must match the alarm definition's machine. |
| `alarm_definition_id` | UUID | Yes | Existing `AlarmDefinition.id` | — | Composite FK with `machine_id` to `alarm_definition`. |
| `external_event_id` | VARCHAR(160) | Yes | Immutable source event ID | Global unique | Required idempotency key; immutable. |
| `event_at` | TIMESTAMPTZ | Yes | UTC source event timestamp | — | Required; retained as source chronology. |
| `received_at` | TIMESTAMPTZ | Yes | UTC receipt timestamp | — | Required. |
| `status` | VARCHAR(32) | Yes | `active`, `cleared` | — | Enum check; defaults to `active`. |
| `cleared_at` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, at or after `event_at`. |
| `issue_type` | VARCHAR(64) | No | Documented issue label or null | — | Optional label; not an independent detector feature. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; row is immutable. |

### OperatorShift

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `shift_code` | VARCHAR(96) | Yes | Immutable shift identifier | Global unique | Immutable after insert. |
| `operator_code` | VARCHAR(96) | Yes | Pseudonymous operator code | — | Required; no personal name is required. |
| `starts_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `ends_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Must be later than `starts_at`. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |

### Acknowledgement

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `business_id` | VARCHAR(192) | Yes | `ACK-<alarm-id>-<2-digit-counter>`; `<alarm-id>` is the immutable `AlarmEvent.external_event_id` | Global unique | Immutable; generated consistently for the alarm event. |
| `alarm_event_id` | UUID | Yes | Existing `AlarmEvent.id` | — | FK to exactly one alarm event. |
| `operator_shift_id` | UUID | Yes | Existing `OperatorShift.id` | — | FK to exactly one operator shift. |
| `acknowledged_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required; may precede the alarm only for a labelled anomaly. |
| `status` | VARCHAR(32) | Yes | `acknowledged`, `late`, `excused` | — | Enum check. |
| `issue_type` | VARCHAR(64) | No | Documented issue label or null | — | Optional anomaly/exception label. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |

An alarm has zero or more acknowledgements; acknowledgement is not mandatory for each alarm.

### Incident

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `business_id` | VARCHAR(64) | Yes | `INC-<year>-<6-digit-counter>` | Global unique | Immutable; generated from the documented counter/year. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | — | FK to exactly one machine. |
| `opened_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `status` | VARCHAR(32) | Yes | `open`, `investigating`, `resolved`, `closed` | — | Enum check; defaults to `open`. |
| `issue_type` | VARCHAR(64) | Yes | Documented incident classification | — | Required; the deliberate missing-context case is retained and labelled. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

### CorrectiveAction

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `business_id` | VARCHAR(192) | Yes | `ACT-<incident-id>-<2-digit-counter>`; `<incident-id>` is the immutable `Incident.business_id` | Global unique | Immutable; generated consistently for the incident. |
| `incident_id` | UUID | Yes | Existing `Incident.id` | — | FK to exactly one incident. |
| `owner` | VARCHAR(128) | Yes | Assignee/owner code | — | Required. |
| `due_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required. |
| `status` | VARCHAR(32) | Yes | `open`, `in_progress`, `completed`, `closed`, `cancelled` | — | Enum check; defaults to `open`. |
| `completed_at` | TIMESTAMPTZ | No | UTC workflow completion timestamp or null | — | If present, validate chronology against the action lifecycle history, not the database insert time. |
| `verification_result` | VARCHAR(32) | Yes | `not_started`, `passed`, `failed`, `not_required` | — | Enum check; defaults to `not_started`. |
| `verification_evidence` | TEXT | No | Evidence reference/text or null | — | Optional; retain evidence when available. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |
| `updated_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; updated by application on row changes. |

The database permits `closed` with no verification result/evidence so the required labelled exception can be represented and detected by validation rather than silently discarded.

## History and bridge records

### MachineStateEvent (MachineStateTransition)

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | — | FK to exactly one machine. |
| `interlock_id` | UUID | No | Existing `Interlock.id` or null | — | Optional composite FK requires the same machine as `machine_id`. |
| `state` | VARCHAR(32) | Yes | `running`, `stopped`, `fault`, `interlock_engaged`, `interlock_bypassed`, `maintenance` | — | Enum check. |
| `interlock_state` | VARCHAR(32) | No | `open`, `closed`, `energized`, `deenergized` or null | — | Enum check; optional when no interlock-state observation exists. |
| `event_at` | TIMESTAMPTZ | Yes | UTC source event timestamp | — | Required; events are ordered by source timestamp. |
| `source_event_id` | VARCHAR(160) | Yes | Immutable source event ID | Global unique | Required; identifies the source transition event. |
| `issue_type` | VARCHAR(64) | No | Documented issue label or null | — | Optional label; ground truth remains a separate output, not a detector input. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; row cannot be updated/deleted. |

### MachineSafetyZone bridge

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID. |
| `machine_id` | UUID | Yes | Existing `Machine.id` | Part of composite FK | Composite FK includes `plant_id`. |
| `safety_zone_id` | UUID | Yes | Existing `SafetyZone.id` | Part of composite FK | Composite FK includes `plant_id`. |
| `plant_id` | UUID | Yes | Existing plant shared by both endpoints | — | Both composite FKs enforce same-plant association. |
| `effective_from` | TIMESTAMPTZ | Yes | UTC timestamp | `(machine_id, safety_zone_id, effective_from)` | Required; enables successive assignments. |
| `effective_to` | TIMESTAMPTZ | No | UTC timestamp or null | — | If present, later than `effective_from`. |

### IncidentAlarmEvent bridge

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID. |
| `incident_id` | UUID | Yes | Existing `Incident.id` | Part of pair uniqueness | FK to `incident.id`. |
| `alarm_event_id` | UUID | Yes | Existing `AlarmEvent.id` | Part of pair uniqueness | FK to `alarm_event.id`. |
| `linked_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`. |

The pair `(incident_id, alarm_event_id)` is unique; each incident and alarm can participate in multiple links.

### LifecycleHistory

| Field | PostgreSQL type | Required | Allowed values / units | Uniqueness | Validation |
|---|---|---:|---|---|---|
| `id` | UUID | Yes | UUID | Primary key | Generated UUID; immutable. |
| `entity_type` | VARCHAR(32) | Yes | `plant`, `machine`, `safety_zone`, `interlock`, `alarm_definition`, `alarm_event`, `operator_shift`, `acknowledgement`, `incident`, `corrective_action`, `machine_state_event` | — | Enum check. |
| `entity_id` | UUID | Yes | UUID of the row named by `entity_type` | — | Polymorphic reference; checked by application validation because target table varies. |
| `transition` | VARCHAR(64) | Yes | Transition name | — | Required; transition vocabulary is defined by the workflow. |
| `occurred_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Required; source chronology. |
| `actor_reference` | VARCHAR(160) | No | Actor/system reference or null | — | Optional; use a code/reference, not personal data. |
| `reason` | TEXT | No | Reason or null | — | Optional explanation. |
| `payload` | JSONB (JSON elsewhere) | Yes | Immutable JSON object | — | Defaults to `{}`; must be JSON serializable. |
| `created_at` | TIMESTAMPTZ | Yes | UTC timestamp | — | Database default `now()`; row cannot be updated/deleted. |

## Cross-cutting database validation

- UUID primary keys and all listed foreign keys are enforced by PostgreSQL.
- Status/state/severity/verification enums are restricted to the values listed above.
- Effective intervals, shift intervals, alarm clear times, and non-negative acknowledgement SLA use database checks. Corrective-action completion and verification chronology is validated against source and lifecycle records, not database insert time.
- Scoped unique keys are enforced on `(plant_id, machine_code)`, `(plant_id, zone_code)`, `(machine_id, alarm_code)`, `(machine_id, interlock_code)`, and bridge pairs/effective starts as listed.
- Alarm definitions and events use a composite FK so an event cannot pair a definition with a different machine.
- Machine-to-zone bridge rows use composite FKs so a machine cannot be linked to a zone in another plant.
- `AlarmEvent.external_event_id` is unique for idempotent event identification. Replay handling must not create another event with the same external ID.
- Acknowledgement chronology relative to an alarm and closure verification are intentionally not hard-rejected; the required anomaly rows need to be persisted with their issue label for validation.
- Append-only event tables retain source event timestamps; the API rejects a newly ingested event that predates the latest alarm or machine-state event for that machine.

## Deterministic API workflow rules

- Alarm severity, acknowledgement requirement, and SLA come from the effective `AlarmDefinition` at the source event time. An acknowledgement is `late` only when elapsed time is greater than the configured SLA; equality is within SLA. The API rejects acknowledgement timestamps before the alarm and timestamps outside the referenced operator shift.
- The default generator configuration assigns acknowledgement SLAs of 60 seconds for critical, 300 seconds for high, 900 seconds for medium, 1800 seconds for low, and 3600 seconds for info alarms. Acknowledgement is required for every generated severity except info; stored effective `AlarmDefinition` values remain authoritative.
- A high/critical alarm creates an incident when a `fault` or `interlock_bypassed` state event exists for the same machine during the preceding five minutes, including the alarm time. Automatically opened incidents use the event time plus one second and link the triggering alarm. A severe alarm without that context is returned with an exception and does not create an incident.
- Alarm ingestion is idempotent on `external_event_id`; state ingestion is idempotent on `source_event_id`. An identical replay returns the prior record; reuse with a different payload returns HTTP 409. Database rows and source event timestamps are not overwritten.
- Acknowledgement business IDs follow `ACK-<AlarmEvent.external_event_id>-NN`; incident IDs follow `INC-<opened-year>-NNNNNN`; corrective-action IDs follow `ACT-<Incident.business_id>-NN`.
- Incident transitions are `open → investigating → resolved → closed`. An incident cannot be resolved or closed until it has at least one action and all actions are `closed` with `verification_result=passed`.
- Corrective actions transition `open → in_progress → completed → closed`, with cancellation permitted from `open` or `in_progress`. The API does not permit direct closure. A completed action closes only after a passed verification with evidence; a failed verification leaves it completed for follow-up.
- Every ingestion and lifecycle transition appends a `LifecycleHistory` row. Audit payloads retain source IDs, prior/current statuses, decisions, or verification evidence as applicable.
- List APIs use `limit`/`offset`, cap `limit` at 200, and order by event/lifecycle time followed by immutable UUID. Time filters use inclusive `from_at` and exclusive `to_at`.
- Recurring alarm clusters are derived from source `AlarmEvent` rows: same machine and alarm definition, at least three events, and no gap greater than one minute between consecutive events. Nuisance patterns are low/medium/info-severity clusters; cluster size is returned from source events rather than independently labelled.
- Read list APIs use server-side paging and documented filters. Machine, alarm, incident, corrective-action, and alarm-cluster search is case-insensitive and does not change stored source records.

## Analytics and traceability outputs

All values in `/api/v1/analytics/summary` are calculated from persisted source rows. `ground_truth` is not loaded by the API or used to calculate a metric.

- `severe_alarm_event_count` is the sum of high and critical source alarm events; `nuisance_alarm_event_count` is info, low, and medium. Cluster counts reuse the same three-event/one-minute grouping rule and divide clusters into nuisance and severe by configured alarm severity. No additional flood threshold is implied. Replay deliveries deduplicated by `external_event_id` do not increase the persisted event count.
- Acknowledgement response is `acknowledged_at - event_at`. A non-negative response at or below the configured SLA is timely, including equality; a response over the SLA is late. Acknowledgements before the alarm are counted as chronology exceptions and excluded from the mean. `unacknowledged_required_alarm_event_count` counts required alarms with no acknowledgement row. `acknowledgement_sla_exception_count` is the sum of late responses, acknowledgements before the alarm, and required alarms without an acknowledgement. The mean is null when there are no non-negative response observations.
- A corrective action is verified when `verification_result=passed`. A closed action with any other result is reported as an exception; it remains visible and is not silently repaired.
- An incident is traceable when it has linked source alarm events, a valid preceding machine-state reference in its lifecycle evidence, incident lifecycle history, and lifecycle history for each linked corrective action. Action records are included in evidence when present; a newly opened incident need not already have an action. Closed unverified actions are reported as traceability exceptions. The incident evidence API returns these records and explicit exception codes; the analytics summary reports counts of traceable incidents and exception categories.
- The labelled contextless incident is expected to appear as missing source-alarm and triggering-state context. Its label is only compared with source-derived findings by generator validation; it does not exempt the incident from traceability reporting.

### Expected metric cases

| Case | Expected result |
|---|---|
| Normal acknowledgement exactly at the configured 300-second SLA | Timely count increases; late and chronology-exception counts do not. Response time contributes 300 seconds to the mean. |
| Boundary acknowledgement at 301 seconds | Late count and SLA-exception count increase by one. |
| Required alarm with no acknowledgement | Unacknowledged-required count and SLA-exception count increase by one. |
| Acknowledgement timestamp before its alarm (labelled evaluation anomaly) | Before-alarm count increases; the timestamp is excluded from response-time mean; the API does not admit this chronology through its write endpoint. |
| Three same-machine, same-definition alarms with consecutive gaps no greater than one minute | One recurring cluster is reported and classified by configured severity. |
| Closed corrective action without passed verification (labelled evaluation anomaly) | Closed-unverified action count increases and remains an exception. |
| Incident with no alarm or preceding unsafe machine-state context (labelled evaluation anomaly) | Incident evidence reports missing source alarm and triggering state; the exception remains visible rather than being treated as traceable. |

### Implementation assumptions

- The stored alarm definition is the authoritative severity/SLA configuration; no independent severity classifier is introduced.
- Unsafe context is defined narrowly as a preceding `fault` or `interlock_bypassed` machine state within five minutes. This deterministic rule operationalizes the supplied unsafe-combination requirement without adding an AI or external-system dependency.
- Database operations use PostgreSQL transactions and a transaction-scoped advisory lock for deterministic incident sequence allocation.
