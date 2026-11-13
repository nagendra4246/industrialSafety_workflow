# Industrial Safety Workflow Implementation Plan

## Repository inspection

The supplied project directory was empty. No README, configuration, application entry points, tests, or Git metadata were present. The paths below are a proposed layout, not existing repository conventions.

## Incremental phases

Each phase is limited to the files listed for it, except for directly necessary tests and updates to those same phase-owned files. Do not start a later phase until the current phase's checks pass.

| Phase | Scope | Proposed files |
|---|---|---|
| 1 | Establish the project skeleton and local developer commands. | `README.md`, `.gitignore`, `docker-compose.yml`, `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `frontend/package.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx` |
| 2 | Define the relational model, constraints, lifecycle enums, and data dictionary. | `backend/app/models.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/`, `backend/tests/conftest.py`, `backend/tests/test_schema_constraints.py`, `backend/pytest.ini`, `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/Dockerfile`, `docs/data-dictionary.md`, `README.md` |
| 3 | Implement deterministic synthetic generation, validation, labelled edge cases, and reproducible exports. | `backend/app/generation/dataset.py`, `ids.py`, `master_data.py`, `events.py`, `edge_cases.py`, `lifecycle.py`, `generator.py`, `validation.py`, `cli.py`, `backend/app/exports/`, `backend/tests/test_generation.py`, `docker-compose.yml`, `README.md`, `docs/data-dictionary.md` |
| 4 | Add data validation, deterministic derived outcomes, and reproducible export/manifest generation. Keep ground truth separate from detector inputs. | `backend/app/validation/`, `backend/app/exports/`, `backend/tests/test_validation.py`, `backend/tests/test_exports.py`, `docs/data-dictionary.md`, `README.md` |
| 5 | Implement persistence and API workflows for events, state history, acknowledgements, incidents, and corrective actions. | `backend/app/api/`, `backend/app/services/event_ingestion.py`, `state_history.py`, `acknowledgements.py`, `incidents.py`, `corrective_actions.py`, `backend/app/db.py`, `backend/tests/test_api.py`, `backend/tests/test_lifecycle.py` |
| 6 | Implement deterministic unsafe-condition detection, repeated/nuisance alarm analysis, prioritization, and measurable evaluation outputs. | `backend/app/analytics/rules.py`, `metrics.py`, `backend/app/services/analysis.py`, `backend/tests/test_rules.py`, `backend/tests/test_metrics.py` |
| 7 | Build the responsive React workflow views for events, machine state, acknowledgements, incidents, actions, exceptions, and analytics. | `frontend/src/api/`, `frontend/src/components/`, `frontend/src/features/`, `frontend/src/styles/`, `frontend/src/App.tsx`, `frontend/src/**/*.test.tsx` |
| 8 | Integrate the services and PostgreSQL for local execution; verify the full workflow through Compose. | `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `backend/app/settings.py`, `backend/tests/test_integration.py`, `README.md` |
| 9 | Run end-to-end acceptance, verify fixed-seed regeneration and checksums, and finalize operator/developer documentation and generated-data outputs. | `backend/tests/test_acceptance.py`, `docs/data-dictionary.md`, `docs/validation-summary.md`, `README.md`; generated outputs under `data/exports/` and `data/manifest.json` |

## Required entities and relationships

The ten core entities are `Plant`, `Machine`, `SafetyZone`, `Interlock`, `AlarmDefinition`, `AlarmEvent`, `OperatorShift`, `Acknowledgement`, `Incident`, and `CorrectiveAction`. Every entity has an immutable primary key; each relationship is enforced by a foreign key. Short codes use scoped uniqueness: `(plant_id, machine_code)`, `(plant_id, zone_code)`, and `(machine_id, alarm_code)`. Any stated external identifiers are immutable and uniquely constrained.

| Relationship | Cardinality and optionality |
|---|---|
| Plant → Machine | One plant has zero or more machines; each machine belongs to exactly one plant. |
| Plant → SafetyZone | One plant has zero or more zones; each zone belongs to exactly one plant. |
| Machine ↔ SafetyZone | Many-to-many through `MachineSafetyZone`; either side may have zero or more bridge rows. The bridge records effective dates. |
| Machine → Interlock | One machine has zero or more interlocks; each interlock belongs to exactly one machine. |
| Machine → AlarmDefinition | One machine has zero or more alarm definitions; each definition belongs to exactly one machine. |
| AlarmDefinition → AlarmEvent | One definition has zero or more alarm events; each event references exactly one definition. |
| Machine → AlarmEvent | One machine has zero or more alarm events; each event references exactly one machine. |
| Machine → MachineStateEvent | One machine has zero or more immutable state events; each state event references exactly one machine. |
| OperatorShift → Acknowledgement | One shift has zero or more acknowledgements; each acknowledgement belongs to exactly one shift. |
| AlarmEvent → Acknowledgement | One alarm has zero or more acknowledgements; each acknowledgement belongs to exactly one alarm. |
| Incident ↔ AlarmEvent | Many-to-many through `IncidentAlarmEvent`; either side may have zero or more bridge rows. |
| Incident → CorrectiveAction | One incident has zero or more corrective actions; each action references exactly one incident. |
| LifecycleHistory → tracked entity | Append-only records identify entity type and ID, transition, timestamp, actor/reference, and immutable payload or reason. This is a polymorphic audit reference, not a foreign key to every entity table. |

Use effective dates for applicable plant, machine, zone, interlock, and alarm-definition relationships/configuration. Preserve state and lifecycle history as records; do not overwrite prior state. Include required workflow fields: acknowledgement timestamp/status/issue type; incident opened timestamp/status/issue type; corrective-action assignee/owner, due date, status, completion timestamp, and verification result/evidence.

`MachineStateEvent` (also called `MachineStateTransition`) is immutable and records an ID, `machine_id`, state, event timestamp, source-event ID, and optional issue/ground-truth label. `LifecycleHistory` is append-only and records an ID, entity type/ID, transition, timestamp, actor/reference, and immutable payload or reason. Ground-truth labels belong only in the separate label/output path and must not be used as detector inputs.

## Requirement-to-test matrix

Tests below are proposed for the phases listed above. They are derived only from the supplied business problem and acceptance criteria.

| Requirement | Proposed verification |
|---|---|
| Exactly ten core entities, plus state history, lifecycle history, and the two many-to-many bridges; all primary and foreign keys and documented cardinalities/optionality. | Model/schema tests assert entity/table set, PK/FK constraints, bridge uniqueness, nullable relationships, and relationship cardinalities. |
| Scoped codes and immutable IDs follow the stated naming patterns. | Model and generator tests accept valid IDs, reject duplicate scoped codes, and verify ID stability/referential consistency across exports. |
| Generate 4 plants, 60 machines, 200 alarm definitions, and 50,000 alarm events over 30 days, default seed `20261022`; parameterized scale uses the same generator. | CLI acceptance test checks exact full-scale counts, time span, default seed, and a smaller scale run. |
| Generate master data before dependent records; use valid parent IDs and preserve event ordering/history. | Generator/validation tests verify all joins and timestamp ordering, including state transitions, alarms, acknowledgements, incidents, and actions. |
| Baseline records precede injection of only the five specified labelled edge cases. | Generator tests verify baseline validity and presence/type of each labelled case: acknowledgement before alarm; impossible interlock state; incident without triggering context; replay-duplicated alarm flood; action closed without verification. |
| Derived values and labels follow documented rules; ground truth is separate and never a detector input. | Rule/generator tests recompute derived outcomes from source records and assert detector feature inputs exclude ground-truth fields/files. |
| Validate IDs, PK/FK joins, enums, chronological ordering, reconciliation, lifecycle transitions, duplicates, and reproducibility. | Focused validation tests cover each category; repeat generation with the same seed and compare row counts and checksums. |
| Include alarm floods, delayed acknowledgements, nuisance alarms, and genuine high-severity incidents. | Distribution/coverage tests assert each documented category is present and severity prioritization is deterministic. |
| Workflow supports idempotent event handling, deterministic rules, acknowledgement timing/SLA exceptions, incident/action verification, and traceability. | API/service tests replay events, exercise timing exceptions and lifecycle transitions, and trace incident records to source alarms, machine-state events, actions, and audit history. |
| Identify repeated unsafe conditions and nuisance clusters; expose key decisions/exceptions and measurable outputs. | Analytics tests use known fixtures to verify repeated-condition grouping, nuisance clusters, severity ordering, and metric values. |
| React UI, FastAPI backend, PostgreSQL persistence, Docker Compose local environment; responsive, low-latency interactions. | API and Compose integration checks; frontend tests for key workflow states and non-blocking interaction patterns; smoke-test the local application. |
| Export CSV, JSON, SQL seed data, schema/data dictionary, generator command, fixed seed, source manifest, checksums, and validation summary. | Export acceptance test checks required artifact types/metadata and validates checksums and summary against generated records. |
| Use lawful synthetic data unless any optional external source has documented provenance and terms. | Documentation/export checks verify source manifest fields for any external data; default generation requires no external dataset. |

## Explicit exclusions

- Image-06 leave management, expense management, approval routing, and business-request roles are unrelated and excluded.
- The image-05 AI Investigation Assistant is outside the supplied problem and excluded.
- Do not add a quarantine/data-quality product domain, authentication/RBAC, external connectors, notifications, or operational metrics as product features. Validation may report invalid generated records as required.

## Unresolved implementation assumptions

- **Assumption — repository layout:** Since the project directory is empty, the paths in the phase table are proposed and may be adjusted to established conventions if a repository is added.
- **Assumption — database migration tool and ORM:** The problem mandates PostgreSQL but does not name a Python ORM or migration tool; select one during Phase 1/2 based on the chosen dependency set.
- **Assumption — lifecycle enum vocabulary:** The problem requires lifecycle states but does not enumerate every allowed value. Define and document the minimum values needed by the stated workflow before implementing validation.
- **Assumption — effective-date semantics:** Effective intervals are represented with start/end timestamps and validated for ordering; overlap rules depend on the specific relationship and should be defined with the schema.
- **Assumption — event time and timezone:** Persist timestamps in UTC and retain source event timestamps; the required 30-day generation window is interpreted as a contiguous interval.
- **Assumption — acknowledgement/SLA and incident rules:** Exact thresholds and unsafe-combination predicates are not specified. Make deterministic, documented rules configurable or centrally defined, and do not infer them from reference images.
- **Assumption — output location and SQL format:** Place generated deliverables under `data/exports/`; SQL seed output is a portable set of PostgreSQL inserts unless a specific dump format is later required.
- **Assumption — source manifest:** Synthetic generation records generator version, seed, parameters, and checksums; external-source provenance fields are populated only if external data is later approved and used.
