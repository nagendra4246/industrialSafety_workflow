Industrial Safety Workflow
A rule-driven OT safety event, machine-state, acknowledgement, incident, and corrective-action workflow with automated exception detection, traceability, and audit history.





Industrial Safety Workflow brings plant topology, machine/interlock state histories, alarm events, operator acknowledgements, safety incidents, and corrective actions into one application. It uses deterministic business rules and SQL-based analytics to prioritize critical hazards, detect recurring alarm patterns, enforce workflow controls, and preserve traceable records.
Project scope: This is a local demonstration built and evaluated with deterministic synthetic data. It is not connected to live PLC/SCADA equipment and does not claim production deployment or certified industrial-safety functionality.

Contents
- Problem
- Solution and key capabilities
- Screenshots
- System architecture
- Safety workflow
- Database design
- Synthetic data generation
- Technology stack
- Run locally
- Database migrations and tests
- Generate and load synthetic data
- Performance validation
- Project structure
- Limitations and next steps
Problem
Industrial safety operations involve telemetry and human follow-through across machine states, safety interlocks, alarm definitions, acknowledgement SLAs, incident escalations, and maintenance actions. When these records are handled in separate systems, it becomes harder to:
- Distinguish critical hazards from nuisance alarm chatter.
- Correlate an alarm with the machine's recent fault or bypassed-interlock state.
- Track operator acknowledgement timing and SLA exceptions.
- Preserve evidence and lifecycle history for each incident.
- Ensure corrective actions are completed and verified before closure.
- Identify repeated alarm patterns and machines that may need preventive maintenance.
Solution and key capabilities
The application provides a React workspace backed by a versioned FastAPI REST API and PostgreSQL.
Implemented capabilities
- Plant and equipment master data: plants, machines, safety zones, interlocks, alarm definitions, and operator shifts.
- Machine-state ingestion: validates and stores machine/interlock state history.
- Alarm ingestion: validates alarm definitions and accepts events idempotently using immutable source identifiers.
- Rule-based incident creation: a HIGH or CRITICAL alarm creates an incident only when a FAULT or BYPASSED interlock state was recorded for the same machine during the preceding five minutes.
- Acknowledgement tracking: records operator acknowledgements and evaluates response time against the SLA stored with the alarm definition.
- Incident evidence and lifecycle: links evidence and status transitions to incidents.
- Corrective-action controls: requires action completion and successful verification before action closure; incident resolution/closure is blocked until every linked action is verified and closed.
- Alarm analytics: identifies recurring clusters of at least three matching alarms on the same machine with consecutive gaps of no more than 60 seconds.
- Data quality and reproducibility: deterministic data generation, relational/lifecycle validation, checksums, manifests, and separate ground-truth evaluation files.
- Responsive UI: overview, machines, alarm events, acknowledgements, incidents, corrective actions, and analytics.
Screenshots
Safety overview

Machines and alarm events
Machine register	Alarm event stream
	


Acknowledgements and incidents
Acknowledgement history	Incident register
	


Corrective actions and analytics
Corrective actions	Analytics dashboard
	


System architecture
The React application is served locally and sends relative /api/ requests through the frontend proxy to FastAPI. The backend applies validation and workflow rules, then uses SQLAlchemy/database access to read and write PostgreSQL. The synthetic-data generator produces reproducible exports and a SQL seed file.

Request and database interaction
1. A user interacts with the React UI.
2. The frontend API client sends an HTTP request to /api/v1/....
3. The frontend development server proxies the request to the FastAPI service.
4. FastAPI routes the request to the relevant workflow or analytics service.
5. The service validates the request and applies business rules.
6. SQLAlchemy executes database operations against PostgreSQL.
7. FastAPI returns a JSON response to the React application.

Safety workflow
The main event path starts with machine-state and alarm ingestion. Alarm events are validated and checked for duplicate source IDs. The service then evaluates alarm severity and recent machine-state history before deciding whether to create an incident.

Core business rules
Area	Rule
Idempotency	Repeated deliveries with the same immutable source ID must not create duplicate source records.
Incident trigger	A HIGH/`CRITICAL` alarm requires a same-machine FAULT or BYPASSED state in the prior five minutes to trigger automatic incident creation.
Acknowledgement SLA	Severity and acknowledgement SLA are evaluated using the stored alarm definition.
Corrective action	An action must be completed and successfully verified before it can be closed.
Incident closure	All linked corrective actions must be verified and closed before incident resolution/closure.
Recurring cluster	At least three matching alarms on the same machine, with consecutive gaps of no more than 60 seconds, form a cluster.


Database design
The database stores plant and machine master data, alarm definitions, machine-state history, alarm events, incidents, incident evidence, corrective actions, action verification, and lifecycle history.

The ER image is a documentation-level view of the entities and relationships. Refer to the Alembic migrations and docs/data-dictionary.md in the source project for the authoritative schema, including exact column types, constraints, indexes, and foreign keys.
Synthetic data generation
The generator uses a fixed seed (20261022) to create a repeatable 30-day dataset:
Dataset entity	Default volume
Plants	4
Machines	60
Alarm definitions	200
Alarm events	50,000


The generation process creates master data before dependent event records, injects labelled anomaly scenarios separately, validates the result, computes checksums, and writes export/report artifacts.

The documented anomaly scenarios include:
- acknowledgement_before_alarm
- impossible_interlock_state
- incident_without_triggering_context
- replay_duplicated_alarm_flood
- action_closed_without_verification
Ground-truth labels and raw replay-delivery payloads are evaluation artifacts and are kept separate from the application seed.sql input to avoid inserting evaluation-only data into source tables.

The checked-in generation manifest described for this project reports all five labelled scenarios detected and matched (F1 = 1.0) by the validator, with zero unlabelled overlaps across the 30 input tables and seed.sql. Treat these as synthetic validation results, not evidence of real-world detection performance.
Technology stack
Layer	Technology	Responsibility
Frontend	React 18	Responsive safety operations UI
Frontend server	Vite / configured frontend container	Local development serving and /api/ proxy
Backend	Python 3.12, FastAPI	Versioned REST API and request handling
Business logic	Python service modules	Ingestion, correlation, acknowledgement, and lifecycle rules
ORM / migrations	SQLAlchemy / Alembic	Database access and schema evolution
Database	PostgreSQL 16	Relational persistence and SQL analytics
Runtime	Docker Compose	Local multi-container environment
Testing	Pytest and frontend test runner	Backend/API/schema and frontend component tests


Run locally
Prerequisites
- Docker Desktop
- Docker Compose
- Git
The application is designed to run through Docker Compose. A host Python virtual environment is not required.
Start the application
git clone <your-repository-url>
cd <your-project-directory>

# Optional: configure local database settings or host ports
cp .env.example .env

docker compose up --build
The Compose defaults work without a .env file. If either host port is already in use, set API_PORT or FRONTEND_PORT in .env.
Local endpoints
Service	URL
React frontend	http://localhost:5173
Frontend health	http://localhost:5173/health
FastAPI health (includes PostgreSQL connectivity)	http://localhost:18000/health
OpenAPI / Swagger UI	http://localhost:18000/docs


PostgreSQL readiness is checked by Compose using pg_isready.
Database migrations and tests
Apply the schema migration to the local application database:
docker compose exec backend alembic upgrade head
Important: The test suite assumes an empty database. Create and migrate a separate test database. Do not run the test suite against the populated application database.
docker compose exec db sh -c \
  'createdb -U "$POSTGRES_USER" industrial_safety_test'

docker compose exec backend sh -c \
  'DATABASE_URL="${DATABASE_URL%/*}/industrial_safety_test" alembic upgrade head && \
   DATABASE_URL="${DATABASE_URL%/*}/industrial_safety_test" pytest'
Run frontend tests and a production build from the frontend/ directory:
npm ci
npm test
npm run build
Generate and load synthetic data
Run the default deterministic generator from the backend container:
docker compose exec backend python -m app.generation.cli
By default, exports are written to data/exports/ on the host. Outputs include CSV, JSON, PostgreSQL seed SQL, ground truth, source manifest, checksums, and machine-readable and human-readable validation reports.
Load into an empty local database
Apply migrations, generate the export, and execute the seed script:
docker compose exec backend alembic upgrade head

docker compose exec backend \
  python -m app.generation.cli --output /data/exports

docker compose exec db sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /data/exports/seed.sql'

docker compose exec db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM alarm_event"'
The final command should report 50000.

Loading is intended for an empty database. The seed process does not truncate or replace existing records. The database container sees the export directory read-only. Ground-truth and replay-delivery files are not inserted into application source tables.
Smaller deterministic smoke run
docker compose exec backend python -m app.generation.cli \
  --plant-count 1 \
  --machine-count 3 \
  --alarm-definition-count 9 \
  --alarm-event-count 40 \
  --days 2
Performance validation
The full default dataset was loaded locally: 4 plants, 60 machines, 200 alarm definitions, and 50,000 alarm events.

The following figures are warm, single-client local Docker Compose measurements. They are not production-capacity or concurrency guarantees.
Request	Median latency
Alarm-event page (50 rows)	9.9 ms
Critical alarm page (50 rows)	13.1 ms
Machine-state page (50 rows)	5.8 ms
Incident page (50 rows)	7.3 ms
Incident evidence	7.6 ms
Analytics summary	163.1 ms
Alarm-cluster page (50 rows)	66.0 ms


The analytics summary median was observed to decrease from 457.5 ms to 163.1 ms, and the cluster-page median from 191.4 ms to 66.0 ms. Baseline measurements used three requests and final measurements used five, so these comparisons are indicative rather than controlled benchmarks.

The PostgreSQL cluster-window query was reported to change from an external sort spilling 3,240 KB to an index-only scan with no heap fetches or disk spill. The composite index follows the cluster partition and ordering columns, and acknowledgement response/SLA statistics are aggregated in SQL.

Re-measure on representative target hardware and under concurrent load before setting operational latency objectives.
Project structure
The paths below describe the major components; adjust them if your repository uses a different layout.
.
├── backend/
│   ├── app/
│   │   ├── generation/
│   │   └── ...
│   ├── alembic/
│   └── tests/
├── frontend/
│   ├── src/
│   ├── tests/
│   └── ...
├── docs/
│   ├── data-dictionary.md
│   └── images/
│       ├── system-architecture.png
│       ├── database-connection-architecture.png
│       ├── database-er-diagram.png
│       ├── safety-workflow-data-flow.png
│       ├── synthetic-data-flow.png
│       └── ...
├── data/
│   └── exports/
├── docker-compose.yml
├── .env.example
└── README.md
Check and stop
docker compose ps
curl --fail http://localhost:5173/
curl --fail http://localhost:5173/health
curl --fail http://localhost:18000/health

docker compose exec db sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

docker compose down
docker compose down stops and removes the containers but preserves the PostgreSQL volume. Add -v only when you intentionally want to delete the local database volume and its data.
Limitations and next steps
- The data is synthetic; no live industrial controller or plant telemetry integration is included.
- The five-minute correlation and 60-second cluster rules are deterministic demonstration rules and should be calibrated with domain experts before operational use.
- The local latency figures are not a substitute for load, resilience, security, or safety validation.
- A production deployment would require authentication and role-based authorization, secure secrets management, transport encryption, observability, backup/recovery procedures, deployment hardening, and OT network/security review.
- The ER diagram is a conceptual illustration; the migrations and data dictionary remain the schema source of truth.
Built as an end-to-end industrial safety workflow demonstration using React, FastAPI, PostgreSQL, and Docker Compose.