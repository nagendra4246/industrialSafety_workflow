import { useEffect, useMemo, useRef, useState } from "react";

import {
  apiGet,
  apiPatch,
  apiPost,
  fetchAll,
  fetchLatestPage,
  fetchNewestPage,
  queryString,
} from "./api.js";
import "./App.css";

const PAGE_SIZE = 20;
const NAV_ITEMS = [
  ["overview", "Overview"],
  ["machines", "Machines"],
  ["alarms", "Alarm events"],
  ["acknowledgements", "Acknowledgements"],
  ["incidents", "Incidents"],
  ["actions", "Corrective actions"],
  ["analytics", "Analytics"],
];
const SEVERITIES = ["critical", "high", "medium", "low", "info"];

function useAsyncData(load, dependencies) {
  const loadRef = useRef(load);
  const [state, setState] = useState({
    data: null,
    error: null,
    loading: true,
  });
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState((previous) => ({
      ...previous,
      loading: true,
      error: null,
    }));
    loadRef
      .current(controller.signal)
      .then((data) => {
        if (active) {
          setState({ data, error: null, loading: false });
        }
      })
      .catch((error) => {
        if (active && error.name !== "AbortError") {
          setState((previous) => ({
            ...previous,
            error: error.message,
            loading: false,
          }));
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, dependencies);

  return state;
}

function useDebouncedValue(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function formatSeconds(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)} s`;
}

function titleCase(value) {
  if (!value) return "Unknown";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function SeverityBadge({ value }) {
  const severity = (value || "unknown").toLowerCase();
  return (
    <span className={`badge severity-${severity}`}>
      {titleCase(severity)}
    </span>
  );
}

function StatusBadge({ value }) {
  const status = (value || "unknown").toLowerCase().replaceAll("_", "-");
  return (
    <span className={`badge status-${status}`}>{titleCase(status)}</span>
  );
}

function LoadingState({ label = "Loading safety data" }) {
  return (
    <div className="state-box" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}…
    </div>
  );
}

function ErrorState({ error, onRetry }) {
  return (
    <div className="state-box state-error" role="alert">
      <div>
        <strong>Could not load this view.</strong>
        <p>{error}</p>
      </div>
      {onRetry && (
        <button className="button button-secondary" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

function EmptyState({ children }) {
  return <div className="state-box state-empty">{children}</div>;
}

function PageHeading({ eyebrow, title, description, actions }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}

function Panel({ title, description, action, children, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <div className="panel-heading">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p className="muted">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function Pagination({ page, total, limit, onChange, disabled = false }) {
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pagination" aria-label="Pagination">
      <span className="muted">
        {total.toLocaleString()} records · Page {Math.min(page + 1, pages)} of{" "}
        {pages}
      </span>
      <div className="button-row">
        <button
          className="button button-secondary"
          disabled={disabled || page === 0 || total === 0}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </button>
        <button
          className="button button-secondary"
          disabled={disabled || page + 1 >= pages}
          onClick={() => onChange(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function MetricCard({ label, value, note, accent = "blue" }) {
  return (
    <article className={`metric-card metric-${accent}`}>
      <p>{label}</p>
      <strong>
        {typeof value === "number" ? value.toLocaleString() : value ?? "0"}
      </strong>
      {note && <span>{note}</span>}
    </article>
  );
}

function FilterField({ label, children }) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function SearchField({ value, onChange, label = "Search" }) {
  return (
    <label className="search-field">
      <span className="sr-only">{label}</span>
      <span aria-hidden="true" className="search-icon">
        ⌕
      </span>
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={label}
      />
    </label>
  );
}

function useLookups() {
  return useAsyncData(
    async (signal) => {
      const [machines, definitions, plants] = await Promise.all([
        fetchAll("/machines", signal),
        fetchAll("/alarm-definitions", signal),
        fetchAll("/plants", signal),
      ]);
      return {
        machineById: new Map(machines.map((machine) => [machine.id, machine])),
        definitionById: new Map(
          definitions.map((definition) => [definition.id, definition]),
        ),
        plantById: new Map(plants.map((plant) => [plant.id, plant])),
      };
    },
    [],
  );
}

function OverviewView({ onNavigate }) {
  const { data, error, loading } = useAsyncData(async (signal) => {
    const [
      summary,
      machines,
      definitions,
      critical,
      high,
      openIncidents,
      investigatingIncidents,
    ] = await Promise.all([
      apiGet("/analytics/summary", { signal }),
      fetchAll("/machines", signal),
      fetchAll("/alarm-definitions", signal),
      fetchLatestPage("/alarm-events", { severity: "critical" }, 5, signal),
      fetchLatestPage("/alarm-events", { severity: "high" }, 5, signal),
      fetchLatestPage("/incidents", { status: "open" }, 5, signal),
      fetchLatestPage("/incidents", { status: "investigating" }, 5, signal),
    ]);
    return {
      summary,
      machines,
      definitions,
      severeAlarms: [...critical.items, ...high.items]
        .sort((left, right) => right.event_at.localeCompare(left.event_at))
        .slice(0, 8),
      openIncidents: [
        ...openIncidents.items,
        ...investigatingIncidents.items,
      ].sort((left, right) => right.opened_at.localeCompare(left.opened_at)),
    };
  }, []);

  const counts = useMemo(() => {
    const summary = data?.summary;
    const severityCounts = summary?.alarm_events_by_severity || {};
    return {
      severe:
        (severityCounts.critical || 0) + (severityCounts.high || 0),
      openIncidents:
        (summary?.incident_count_by_status?.open || 0) +
        (summary?.incident_count_by_status?.investigating || 0),
      verifiedActions:
        summary?.verified_corrective_action_count || 0,
    };
  }, [data]);

  if (loading && !data) return <LoadingState />;
  if (error && !data) return <ErrorState error={error} />;
  const machineById = new Map(
    (data?.machines || []).map((machine) => [machine.id, machine]),
  );
  const definitionById = new Map(
    (data?.definitions || []).map((definition) => [
      definition.id,
      definition,
    ]),
  );
  const severityCounts = data?.summary.alarm_events_by_severity || {};
  const severityMaximum = Math.max(1, ...Object.values(severityCounts));

  return (
    <>
      <PageHeading
        eyebrow="Plant safety"
        title="Safety overview"
        description="A traceable view of alarms, machine conditions, acknowledgements, and corrective work."
        actions={
          <button
            className="button button-primary"
            onClick={() => onNavigate("alarms")}
          >
            Review alarm events
          </button>
        }
      />
      {error && <ErrorState error={error} />}
      <div className="metric-grid">
        <MetricCard
          label="Alarm events"
          value={data?.summary.alarm_event_count}
          note={`${data?.machines.length || 0} machines in view`}
        />
        <MetricCard
          label="High / critical"
          value={counts.severe}
          note="Prioritized severity"
          accent="red"
        />
        <MetricCard
          label="Late acknowledgements"
          value={data?.summary.late_acknowledgement_count}
          note="Outside configured SLA"
          accent="amber"
        />
        <MetricCard
          label="Open incidents"
          value={counts.openIncidents}
          note="Open or investigating"
          accent="violet"
        />
        <MetricCard
          label="Verified actions"
          value={counts.verifiedActions}
          note="Closed after verification"
          accent="green"
        />
        <MetricCard
          label="Recurring clusters"
          value={data?.summary.repeated_alarm_cluster_count}
          note="3+ events within 1 minute"
          accent="teal"
        />
      </div>

      <div className="content-grid overview-grid">
        <Panel
          title="Priority alarm events"
          description="Critical and high-severity events are surfaced first."
          action={
            <button
              className="button button-quiet"
              onClick={() => onNavigate("alarms")}
            >
              View all
            </button>
          }
        >
          {data?.severeAlarms.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">
                  Recent high and critical alarm events
                </caption>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Alarm</th>
                    <th>Machine</th>
                    <th>Occurred</th>
                  </tr>
                </thead>
                <tbody>
                  {data.severeAlarms.map((alarm) => {
                    const definition = definitionById.get(
                      alarm.alarm_definition_id,
                    );
                    return (
                      <tr key={alarm.id}>
                        <td>
                          <SeverityBadge value={definition?.severity} />
                        </td>
                        <td>
                          <strong>{definition?.name || "Alarm event"}</strong>
                          <small>{alarm.external_event_id}</small>
                        </td>
                        <td>
                          {machineById.get(alarm.machine_id)?.machine_code ||
                            alarm.machine_id.slice(0, 8)}
                        </td>
                        <td>{formatTime(alarm.event_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No high or critical alarm events found.</EmptyState>
          )}
        </Panel>

        <Panel
          title="Alarm mix"
          description="Event counts by configured alarm severity."
        >
          <div className="severity-chart">
            {SEVERITIES.map((severity) => {
              const count = severityCounts[severity] || 0;
              return (
                <div className="chart-row" key={severity}>
                  <div className="chart-label">
                    <SeverityBadge value={severity} />
                    <strong>{count.toLocaleString()}</strong>
                  </div>
                  <div
                    className="bar-track"
                    role="img"
                    aria-label={`${severity} alarms: ${count}`}
                  >
                    <span
                      className={`bar-fill severity-${severity}`}
                      style={{
                        width: `${(count / severityMaximum) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      </div>

      <Panel
        title="Incidents needing attention"
        description="Open and investigating incidents with lifecycle traceability."
        action={
          <button
            className="button button-quiet"
            onClick={() => onNavigate("incidents")}
          >
            View incidents
          </button>
        }
      >
        {data?.openIncidents.length ? (
          <div className="incident-strip">
            {data.openIncidents.slice(0, 6).map((incident) => (
              <div className="incident-card" key={incident.id}>
                <div>
                  <strong>{incident.business_id}</strong>
                  <small>
                    {machineById.get(incident.machine_id)?.machine_code ||
                      incident.machine_id.slice(0, 8)}
                  </small>
                </div>
                <StatusBadge value={incident.status} />
                <time dateTime={incident.opened_at}>
                  {formatTime(incident.opened_at)}
                </time>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>No incidents are currently open.</EmptyState>
        )}
      </Panel>
    </>
  );
}

function MachinesView({ onNavigate }) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [selectedMachineId, setSelectedMachineId] = useState(null);
  const [retry, setRetry] = useState(0);
  const resource = useAsyncData(
    (signal) =>
      apiGet(
        `/machines${queryString({
          search: debouncedSearch,
          status,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        })}`,
        { signal },
      ),
    [debouncedSearch, status, page, retry],
  );
  const lookups = useLookups();
  const selectedMachine = resource.data?.items.find(
    (item) => item.id === selectedMachineId,
  );

  useEffect(() => setPage(0), [debouncedSearch, status]);

  return (
    <>
      <PageHeading
        eyebrow="Equipment"
        title="Machines"
        description="Select a machine to inspect its latest state and event history."
      />
      <div className="toolbar">
        <SearchField value={search} onChange={setSearch} label="Search machines" />
        <FilterField label="Machine status">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {["active", "maintenance", "inactive", "decommissioned"].map(
              (value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ),
            )}
          </select>
        </FilterField>
      </div>
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && (
        <ErrorState
          error={resource.error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      )}
      {resource.data && (
        <Panel title="Machine register">
          {resource.data.items.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Machine register</caption>
                <thead>
                  <tr>
                    <th>Machine</th>
                    <th>Plant</th>
                    <th>Status</th>
                    <th>Effective from</th>
                    <th>Context</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((machine) => {
                    const plant = lookups.data?.plantById.get(machine.plant_id);
                    return (
                      <tr key={machine.id}>
                        <td>
                          <strong>{machine.machine_code}</strong>
                          <small>{machine.name}</small>
                        </td>
                        <td>{plant?.plant_code || machine.plant_id.slice(0, 8)}</td>
                        <td>
                          <StatusBadge value={machine.status} />
                        </td>
                        <td>{formatTime(machine.effective_from)}</td>
                        <td>
                          <button
                            className="button button-quiet"
                            aria-label={`View context for ${machine.machine_code}`}
                            aria-expanded={selectedMachineId === machine.id}
                            onClick={() =>
                              setSelectedMachineId((current) =>
                                current === machine.id ? null : machine.id,
                              )
                            }
                          >
                            {selectedMachineId === machine.id
                              ? "Hide details"
                              : "View details"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No machines match this search.</EmptyState>
          )}
          <Pagination
            page={page}
            total={resource.data.total}
            limit={resource.data.limit}
            onChange={setPage}
            disabled={resource.loading}
          />
        </Panel>
      )}
      {selectedMachine && (
        <MachineContext
          machine={selectedMachine}
          definitionById={lookups.data?.definitionById}
          onViewAlarms={() => onNavigate("alarms")}
        />
      )}
    </>
  );
}

function MachineContext({ machine, definitionById, onViewAlarms }) {
  const { data, error, loading } = useAsyncData(async (signal) => {
    const [states, alarms] = await Promise.all([
      fetchLatestPage(`/machines/${machine.id}/state-events`, {}, 10, signal),
      fetchLatestPage(
        "/alarm-events",
        { machine_id: machine.id },
        10,
        signal,
      ),
    ]);
    return { states: states.items, alarms: alarms.items };
  }, [machine?.id]);

  if (!machine) return null;
  const currentState = data?.states[0];
  return (
    <Panel
      title={`${machine.machine_code} · Current and recent context`}
      description={machine.name}
      className="detail-panel"
      action={
        <button className="button button-secondary" onClick={onViewAlarms}>
          Open alarm events
        </button>
      }
    >
      {loading && !data && <LoadingState />}
      {error && <ErrorState error={error} />}
      {data && (
        <div className="content-grid">
          <div>
            <h3>Current recorded state</h3>
            {currentState ? (
              <div className="current-state">
                <StatusBadge value={currentState.state} />
                <span>{formatTime(currentState.event_at)}</span>
                {currentState.interlock_state && (
                  <span>
                    Interlock {titleCase(currentState.interlock_state)}
                  </span>
                )}
              </div>
            ) : (
              <EmptyState>No state transitions have been recorded.</EmptyState>
            )}
            <h3 className="subsection-title">State history</h3>
            {data.states.length ? (
              <ul className="event-list">
                {data.states
                  .slice()
                  .reverse()
                  .map((event) => (
                    <li key={event.id}>
                      <StatusBadge value={event.state} />
                      <span>{formatTime(event.event_at)}</span>
                      <small>{event.source_event_id}</small>
                    </li>
                  ))}
              </ul>
            ) : (
              <EmptyState>No state history available.</EmptyState>
            )}
          </div>
          <div>
            <h3>Recent alarm events</h3>
            {data.alarms.length ? (
              <ul className="event-list">
                {data.alarms.map((event) => (
                  <li key={event.id}>
                    <SeverityBadge
                      value={
                        definitionById?.get(event.alarm_definition_id)?.severity
                      }
                    />
                    <span>{formatTime(event.event_at)}</span>
                    <small>{event.external_event_id}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>No alarm events are recorded for this machine.</EmptyState>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

function AlarmEventsView({ onNavigate }) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [selectedAlarmId, setSelectedAlarmId] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const resource = useAsyncData(
    async (signal) => {
      const [events, definitions, machines] = await Promise.all([
        fetchNewestPage(
          "/alarm-events",
          { search: debouncedSearch, severity, status },
          page,
          PAGE_SIZE,
          signal,
        ),
        fetchAll("/alarm-definitions", signal),
        fetchAll("/machines", signal),
      ]);
      return {
        events,
        definitionById: new Map(
          definitions.map((definition) => [definition.id, definition]),
        ),
        machineById: new Map(machines.map((machine) => [machine.id, machine])),
      };
    },
    [debouncedSearch, severity, status, page, refresh],
  );
  useEffect(() => setPage(0), [debouncedSearch, severity, status]);
  const selectedAlarm = resource.data?.events.items.find(
    (event) => event.id === selectedAlarmId,
  );

  return (
    <>
      <PageHeading
        eyebrow="Event stream"
        title="Alarm events"
        description="Search and filter the source event stream. High and critical alarms are clearly prioritized."
      />
      <div className="toolbar">
        <SearchField
          value={search}
          onChange={setSearch}
          label="Search event ID or alarm"
        />
        <FilterField label="Severity">
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
          >
            <option value="">All severities</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="Event status">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="cleared">Cleared</option>
          </select>
        </FilterField>
      </div>
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && <ErrorState error={resource.error} />}
      {resource.data && (
        <Panel
          title="Alarm event list"
          description="Newest matching events appear first."
        >
          {resource.data.events.items.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Alarm event list</caption>
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Alarm</th>
                    <th>Machine</th>
                    <th>Event time</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.events.items.map((event) => {
                    const definition = resource.data.definitionById.get(
                      event.alarm_definition_id,
                    );
                    const machine = resource.data.machineById.get(
                      event.machine_id,
                    );
                    return (
                      <tr
                        key={event.id}
                        className={
                          ["critical", "high"].includes(definition?.severity)
                            ? "priority-row"
                            : ""
                        }
                      >
                        <td>
                          <SeverityBadge value={definition?.severity} />
                        </td>
                        <td>
                          <strong>{definition?.name || "Alarm"}</strong>
                          <small>{event.external_event_id}</small>
                        </td>
                        <td>{machine?.machine_code || event.machine_id}</td>
                        <td>{formatTime(event.event_at)}</td>
                        <td>
                          <StatusBadge value={event.status} />
                        </td>
                        <td>
                          <div className="button-row">
                            <button
                              className="button button-quiet"
                              onClick={() =>
                                setSelectedAlarmId((current) =>
                                  current === event.id ? null : event.id,
                                )
                              }
                              aria-expanded={selectedAlarmId === event.id}
                            >
                              Details
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No alarm events match these filters.</EmptyState>
          )}
          <Pagination
            page={page}
            total={resource.data.events.total}
            limit={resource.data.events.limit}
            onChange={setPage}
            disabled={resource.loading}
          />
        </Panel>
      )}
      {selectedAlarm && resource.data && (
        <AlarmDetail
          alarm={selectedAlarm}
          definition={resource.data.definitionById.get(
            selectedAlarm.alarm_definition_id,
          )}
          machine={resource.data.machineById.get(selectedAlarm.machine_id)}
          onClose={() => setSelectedAlarmId(null)}
          onAcknowledged={() => setRefresh((value) => value + 1)}
          onViewIncidents={() => onNavigate("incidents")}
        />
      )}
    </>
  );
}

function AlarmDetail({
  alarm,
  definition,
  machine,
  onClose,
  onAcknowledged,
  onViewIncidents,
}) {
  const [showAcknowledgement, setShowAcknowledgement] = useState(false);
  const context = useAsyncData(
    (signal) =>
      apiGet(
        `/machines/${alarm.machine_id}/state-events${queryString({
          from_at: new Date(
            new Date(alarm.event_at).getTime() - 5 * 60 * 1000,
          ).toISOString(),
          to_at: new Date(
            new Date(alarm.event_at).getTime() + 1,
          ).toISOString(),
          limit: 50,
          offset: 0,
        })}`,
        { signal },
      ),
    [alarm.id],
  );
  return (
    <Panel
      title="Alarm event detail"
      description={alarm.external_event_id}
      className="detail-panel"
      action={
        <button className="button button-quiet" onClick={onClose}>
          Close details
        </button>
      }
    >
      <div className="detail-grid">
        <div>
          <span className="detail-label">Configured alarm</span>
          <strong>{definition?.name || "Unknown alarm"}</strong>
          <SeverityBadge value={definition?.severity} />
        </div>
        <div>
          <span className="detail-label">Machine</span>
          <strong>{machine?.machine_code || alarm.machine_id}</strong>
        </div>
        <div>
          <span className="detail-label">Occurred / received</span>
          <strong>{formatTime(alarm.event_at)}</strong>
          <small>Received {formatTime(alarm.received_at)}</small>
        </div>
        <div>
          <span className="detail-label">Acknowledgement SLA</span>
          <strong>
            {definition?.acknowledgement_required
              ? `${definition.ack_sla_seconds} seconds`
              : "Not required"}
          </strong>
        </div>
      </div>
      <div className="button-row detail-actions">
        {definition?.acknowledgement_required && (
          <button
            className="button button-primary"
            onClick={() => setShowAcknowledgement((value) => !value)}
          >
            {showAcknowledgement ? "Hide acknowledgement form" : "Record acknowledgement"}
          </button>
        )}
        <button className="button button-secondary" onClick={onViewIncidents}>
          Review incidents
        </button>
      </div>
      {showAcknowledgement && (
        <AcknowledgementComposer
          alarm={alarm}
          onCancel={() => setShowAcknowledgement(false)}
          onSuccess={() => {
            setShowAcknowledgement(false);
            onAcknowledged();
          }}
        />
      )}
      <h3 className="subsection-title">Machine-state context (prior 5 minutes)</h3>
      {context.loading && !context.data && <LoadingState />}
      {context.error && <ErrorState error={context.error} />}
      {context.data?.items.length ? (
        <ul className="event-list">
          {context.data.items
            .slice()
            .reverse()
            .map((event) => (
              <li key={event.id}>
                <StatusBadge value={event.state} />
                <span>{formatTime(event.event_at)}</span>
                {event.interlock_state && (
                  <span>Interlock {titleCase(event.interlock_state)}</span>
                )}
              </li>
            ))}
        </ul>
      ) : (
        !context.loading && (
          <EmptyState>No machine-state event was recorded in this window.</EmptyState>
        )
      )}
      {alarm.issue_type && (
        <p className="exception-note">
          Recorded exception: {titleCase(alarm.issue_type)}
        </p>
      )}
    </Panel>
  );
}

function AcknowledgementComposer({ alarm, onCancel, onSuccess }) {
  const [shiftId, setShiftId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const shifts = useAsyncData(async (signal) => {
    const startsAfter = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    return apiGet(
      `/operator-shifts${queryString({
        starts_after: startsAfter,
        limit: 200,
        offset: 0,
      })}`,
      { signal },
    );
  }, []);
  const eligibleShifts = useMemo(() => {
    const now = Date.now();
    return (shifts.data?.items || []).filter(
      (shift) =>
        new Date(shift.starts_at).getTime() <= now &&
        new Date(shift.ends_at).getTime() > now,
    );
  }, [shifts.data]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await apiPost("/acknowledgements", {
        business_id: `ACK-${alarm.external_event_id}-01`,
        alarm_event_id: alarm.id,
        operator_shift_id: shiftId,
        acknowledged_at: new Date().toISOString(),
      });
      onSuccess();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <div>
        <h3>Record acknowledgement</h3>
        <p className="muted">
          The API calculates acknowledgement status from the configured SLA.
        </p>
      </div>
      {shifts.loading && <LoadingState label="Loading operator shifts" />}
      {shifts.error && <ErrorState error={shifts.error} />}
      {!shifts.loading && !shifts.error && (
        <>
          <label>
            Operator shift
            <select
              required
              value={shiftId}
              onChange={(event) => setShiftId(event.target.value)}
            >
              <option value="">Choose the active shift</option>
              {eligibleShifts.map((shift) => (
                <option key={shift.id} value={shift.id}>
                  {shift.shift_code} · {shift.operator_code}
                </option>
              ))}
            </select>
          </label>
          {!eligibleShifts.length && (
            <p className="exception-note">
              No active operator shift was found. An acknowledgement cannot be
              recorded without a shift covering the current time.
            </p>
          )}
        </>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="button-row">
        <button
          className="button button-primary"
          type="submit"
          disabled={submitting || !shiftId || !eligibleShifts.length}
        >
          {submitting ? "Saving…" : "Save acknowledgement"}
        </button>
        <button
          className="button button-secondary"
          type="button"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function AcknowledgementsView() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const resource = useAsyncData(
    (signal) =>
      fetchNewestPage(
        "/acknowledgements",
        { status },
        page,
        PAGE_SIZE,
        signal,
      ),
    [status, page],
  );
  useEffect(() => setPage(0), [status]);
  return (
    <>
      <PageHeading
        eyebrow="Operator response"
        title="Acknowledgements"
        description="Review acknowledgement timing, SLA exceptions, and the linked shift."
      />
      <div className="toolbar">
        <FilterField label="Acknowledgement status">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            <option value="acknowledged">Acknowledged within SLA</option>
            <option value="late">Late</option>
            <option value="excused">Excused</option>
          </select>
        </FilterField>
      </div>
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && <ErrorState error={resource.error} />}
      {resource.data && (
        <Panel title="Acknowledgement history">
          {resource.data.items.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Acknowledgement history</caption>
                <thead>
                  <tr>
                    <th>Acknowledgement</th>
                    <th>Alarm event</th>
                    <th>Operator shift</th>
                    <th>Time</th>
                    <th>Status / exception</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((ack) => (
                    <tr key={ack.id}>
                      <td><strong>{ack.business_id}</strong></td>
                      <td>{ack.alarm_event_id}</td>
                      <td>{ack.operator_shift_id}</td>
                      <td>{formatTime(ack.acknowledged_at)}</td>
                      <td>
                        <StatusBadge value={ack.status} />
                        {ack.issue_type && (
                          <small className="exception-text">
                            {titleCase(ack.issue_type)}
                          </small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No acknowledgements match this filter.</EmptyState>
          )}
          <Pagination
            page={page}
            total={resource.data.total}
            limit={resource.data.limit}
            onChange={setPage}
            disabled={resource.loading}
          />
        </Panel>
      )}
    </>
  );
}

function IncidentsView() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const resource = useAsyncData(
    (signal) =>
      fetchNewestPage(
        "/incidents",
        { search: debouncedSearch, status },
        page,
        PAGE_SIZE,
        signal,
      ),
    [debouncedSearch, status, page, refresh],
  );
  useEffect(() => setPage(0), [debouncedSearch, status]);

  return (
    <>
      <PageHeading
        eyebrow="Traceability"
        title="Incidents"
        description="Inspect triggering alarm and machine-state evidence, lifecycle history, and linked actions."
      />
      <div className="toolbar">
        <SearchField
          value={search}
          onChange={setSearch}
          label="Search incidents"
        />
        <FilterField label="Incident status">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {["open", "investigating", "resolved", "closed"].map((value) => (
              <option key={value} value={value}>{titleCase(value)}</option>
            ))}
          </select>
        </FilterField>
      </div>
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && <ErrorState error={resource.error} />}
      {resource.data && (
        <Panel title="Incident register">
          {resource.data.items.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Incident register</caption>
                <thead>
                  <tr>
                    <th>Incident</th>
                    <th>Machine</th>
                    <th>Issue</th>
                    <th>Opened</th>
                    <th>Status</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((incident) => (
                    <tr key={incident.id}>
                      <td><strong>{incident.business_id}</strong></td>
                      <td>{incident.machine_id}</td>
                      <td>{titleCase(incident.issue_type)}</td>
                      <td>{formatTime(incident.opened_at)}</td>
                      <td><StatusBadge value={incident.status} /></td>
                      <td>
                        <button
                          className="button button-quiet"
                          aria-expanded={selectedId === incident.id}
                          onClick={() =>
                            setSelectedId((current) =>
                              current === incident.id ? null : incident.id,
                            )
                          }
                        >
                          {selectedId === incident.id ? "Hide" : "Open detail"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No incidents match this filter.</EmptyState>
          )}
          <Pagination
            page={page}
            total={resource.data.total}
            limit={resource.data.limit}
            onChange={setPage}
            disabled={resource.loading}
          />
        </Panel>
      )}
      {selectedId && (
        <IncidentDetails
          incidentId={selectedId}
          onChanged={() => setRefresh((value) => value + 1)}
        />
      )}
    </>
  );
}

function IncidentDetails({ incidentId, onChanged }) {
  const [refresh, setRefresh] = useState(0);
  const [loadError, setLoadError] = useState("");
  const { data, error, loading } = useAsyncData(
    async (signal) => {
      const [evidence, definitions] = await Promise.all([
        apiGet(`/incidents/${incidentId}/evidence`, { signal }),
        fetchAll("/alarm-definitions", signal),
      ]);
      return {
        ...evidence,
        definitionById: new Map(
          definitions.map((definition) => [definition.id, definition]),
        ),
      };
    },
    [incidentId, refresh],
  );

  if (loading && !data) return <LoadingState label="Loading incident evidence" />;
  if (error && !data) return <ErrorState error={error} />;
  const incident = data.incident;
  const nextIncidentStatus = {
    open: "investigating",
    investigating: "resolved",
    resolved: "closed",
  }[incident.status];

  async function transitionIncident() {
    setLoadError("");
    try {
      await apiPatch(`/incidents/${incident.id}/status`, {
        status: nextIncidentStatus,
        occurred_at: new Date().toISOString(),
      });
      setRefresh((value) => value + 1);
      onChanged();
    } catch (requestError) {
      setLoadError(requestError.message);
    }
  }

  return (
    <Panel
      title={`${incident.business_id} · Evidence and lifecycle`}
      description={`${titleCase(incident.issue_type)} · opened ${formatTime(incident.opened_at)}`}
      className="detail-panel"
      action={<StatusBadge value={incident.status} />}
    >
      {error && <ErrorState error={error} />}
      {loadError && <p className="form-error" role="alert">{loadError}</p>}
      <section className="subsection" aria-label="Traceability status">
        <h3>Traceability status</h3>
        <p role={data.traceability.traceable ? "status" : "alert"}>
          {data.traceability.traceable
            ? "Source alarm, machine-state, and lifecycle evidence is linked."
            : `Traceability exceptions: ${data.traceability.exceptions
                .map(titleCase)
                .join(", ")}`}
        </p>
        <p className="muted">
          {data.traceability.source_alarm_event_count} alarm events ·{" "}
          {data.traceability.machine_state_event_count} machine-state events ·{" "}
          {data.traceability.corrective_action_count} corrective actions ·{" "}
          {data.traceability.lifecycle_record_count} lifecycle records (
          {data.traceability.action_lifecycle_record_count} action transitions)
        </p>
      </section>
      <div className="content-grid evidence-grid">
        <section>
          <h3>Triggering alarm events</h3>
          {data.alarm_events.length ? (
            <ul className="event-list">
              {data.alarm_events.map((alarm) => {
                const definition = data.definitionById.get(
                  alarm.alarm_definition_id,
                );
                return (
                  <li key={alarm.id}>
                    <SeverityBadge value={definition?.severity} />
                    <span>{definition?.name || alarm.external_event_id}</span>
                    <small>{formatTime(alarm.event_at)}</small>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState>No triggering alarm links are recorded.</EmptyState>
          )}
        </section>
        <section>
          <h3>Machine-state evidence</h3>
          {data.machine_state_events.length ? (
            <ul className="event-list">
              {data.machine_state_events.map((state) => (
                <li key={state.id}>
                  <StatusBadge value={state.state} />
                  <span>{formatTime(state.event_at)}</span>
                  {state.interlock_state && (
                    <small>Interlock {titleCase(state.interlock_state)}</small>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>No linked machine-state history is recorded.</EmptyState>
          )}
        </section>
      </div>
      <ActionCreateForm
        incident={incident}
        actionCount={data.corrective_actions.length}
        onCreated={() => {
          setRefresh((value) => value + 1);
          onChanged();
        }}
      />
      <section className="subsection">
        <h3>Corrective-action history</h3>
        {data.corrective_actions.length ? (
          <div className="stack-list">
            {data.corrective_actions.map((action) => (
              <ActionCard
                key={action.id}
                action={action}
                onChanged={() => {
                  setRefresh((value) => value + 1);
                  onChanged();
                }}
              />
            ))}
          </div>
        ) : (
          <EmptyState>No corrective actions have been created.</EmptyState>
        )}
      </section>
      <section className="subsection">
        <h3>Lifecycle history</h3>
        {data.lifecycle_history.length ? (
          <ul className="event-list">
            {data.lifecycle_history.map((item) => (
              <li key={item.id}>
                <StatusBadge value={item.transition} />
                <span>{formatTime(item.occurred_at)}</span>
                <small>
                  {titleCase(item.entity_type)} ·{" "}
                  {item.actor_reference || "System"}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>No lifecycle history is recorded.</EmptyState>
        )}
      </section>
      {nextIncidentStatus && (
        <>
          {["resolved", "closed"].includes(nextIncidentStatus) &&
            (!data.corrective_actions.length ||
              data.corrective_actions.some(
                (action) =>
                  action.status !== "closed" ||
                  action.verification_result !== "passed",
              )) && (
              <p className="muted">
                Resolution and closure require all linked actions to be closed
                with a passed verification.
              </p>
            )}
          <button
            className="button button-primary"
            onClick={transitionIncident}
            disabled={
              loading ||
              (["resolved", "closed"].includes(nextIncidentStatus) &&
                (!data.corrective_actions.length ||
                  data.corrective_actions.some(
                    (action) =>
                      action.status !== "closed" ||
                      action.verification_result !== "passed",
                  )))
            }
          >
            Move incident to {titleCase(nextIncidentStatus)}
          </button>
        </>
      )}
    </Panel>
  );
}

function ActionCreateForm({ incident, actionCount, onCreated }) {
  const [expanded, setExpanded] = useState(false);
  const [owner, setOwner] = useState("");
  const [dueDate, setDueDate] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    return date.toISOString().slice(0, 10);
  });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    const createdAt = new Date();
    try {
      await apiPost("/corrective-actions", {
        business_id: `ACT-${incident.business_id}-${String(actionCount + 1).padStart(2, "0")}`,
        incident_id: incident.id,
        owner,
        created_at: createdAt.toISOString(),
        due_at: new Date(`${dueDate}T23:59:59`).toISOString(),
      });
      setOwner("");
      setExpanded(false);
      onCreated();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="subsection">
      <div className="panel-heading">
        <div>
          <h3>Corrective actions</h3>
          <p className="muted">
            Track ownership, due dates, completion, and verification evidence.
          </p>
        </div>
        <button
          className="button button-secondary"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? "Cancel new action" : "Add corrective action"}
        </button>
      </div>
      {expanded && (
        <form className="inline-form form-grid" onSubmit={submit}>
          <label>
            Assignee / owner
            <input
              required
              maxLength={128}
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
            />
          </label>
          <label>
            Due date
            <input
              type="date"
              required
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
            />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button
            className="button button-primary"
            type="submit"
            disabled={submitting}
          >
            {submitting ? "Saving…" : "Create action"}
          </button>
        </form>
      )}
    </section>
  );
}

function ActionCard({ action, onChanged }) {
  const [evidence, setEvidence] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const actions = {
    open: [
      ["in_progress", "Start work"],
      ["cancelled", "Cancel action"],
    ],
    in_progress: [
      ["completed", "Mark completed"],
      ["cancelled", "Cancel action"],
    ],
    completed: [],
    closed: [],
    cancelled: [],
  }[action.status] || [];

  async function changeStatus(status) {
    setError("");
    setBusy(true);
    try {
      await apiPatch(`/corrective-actions/${action.id}/status`, {
        status,
        occurred_at: new Date().toISOString(),
      });
      onChanged();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function verify(result) {
    setError("");
    setBusy(true);
    try {
      await apiPost(`/corrective-actions/${action.id}/verify`, {
        result,
        evidence,
        verified_at: new Date().toISOString(),
      });
      setEvidence("");
      onChanged();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="action-card">
      <summary>
        <span>
          <strong>{action.business_id}</strong>
          <small>{action.owner} · due {formatTime(action.due_at)}</small>
        </span>
        <StatusBadge value={action.status} />
      </summary>
      <div className="action-card-body">
        <p>
          Verification: <StatusBadge value={action.verification_result} />
          {action.completed_at && (
            <span className="muted"> · completed {formatTime(action.completed_at)}</span>
          )}
        </p>
        {action.verification_evidence && (
          <p className="evidence-copy">{action.verification_evidence}</p>
        )}
        {actions.map(([status, label]) => (
          <button
            key={status}
            className={`button ${status === "cancelled" ? "button-secondary" : "button-primary"}`}
            onClick={() => changeStatus(status)}
            disabled={busy}
          >
            {busy ? "Saving…" : label}
          </button>
        ))}
        {action.status === "completed" && (
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              verify("passed");
            }}
          >
            <label>
              Verification evidence
              <textarea
                required
                maxLength={10000}
                rows={3}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
              />
            </label>
            <div className="button-row">
              <button
                className="button button-primary"
                type="submit"
                disabled={busy || !evidence.trim()}
              >
                {busy ? "Saving…" : "Verify and close"}
              </button>
              <button
                className="button button-secondary"
                type="button"
                disabled={busy || !evidence.trim()}
                onClick={() => verify("failed")}
              >
                Record failed verification
              </button>
            </div>
          </form>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
    </details>
  );
}

function CorrectiveActionsView() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const resource = useAsyncData(
    (signal) =>
      apiGet(
        `/corrective-actions${queryString({
          search: debouncedSearch,
          status,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        })}`,
        { signal },
      ),
    [debouncedSearch, status, page, refresh],
  );
  useEffect(() => setPage(0), [debouncedSearch, status]);
  return (
    <>
      <PageHeading
        eyebrow="Follow-through"
        title="Corrective actions"
        description="Move actions through their lifecycle and retain verification evidence before closure."
      />
      <div className="toolbar">
        <SearchField
          value={search}
          onChange={setSearch}
          label="Search owner or action ID"
        />
        <FilterField label="Action status">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {["open", "in_progress", "completed", "closed", "cancelled"].map(
              (value) => (
                <option key={value} value={value}>{titleCase(value)}</option>
              ),
            )}
          </select>
        </FilterField>
      </div>
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && <ErrorState error={resource.error} />}
      {resource.data && (
        <Panel title="Action register">
          {resource.data.items.length ? (
            <div className="stack-list">
              {resource.data.items.map((action) => (
                <ActionCard
                  key={action.id}
                  action={action}
                  onChanged={() => setRefresh((value) => value + 1)}
                />
              ))}
            </div>
          ) : (
            <EmptyState>No corrective actions match this filter.</EmptyState>
          )}
          <Pagination
            page={page}
            total={resource.data.total}
            limit={resource.data.limit}
            onChange={setPage}
            disabled={resource.loading}
          />
        </Panel>
      )}
    </>
  );
}

function AnalyticsView() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(0);
  const resource = useAsyncData(async (signal) => {
    const [summary, clusters] = await Promise.all([
      apiGet("/analytics/summary", { signal }),
      apiGet(
        `/analytics/alarm-clusters${queryString({
          kind,
          search: debouncedSearch,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        })}`,
        { signal },
      ),
    ]);
    return { summary, clusters };
  }, [kind, debouncedSearch, page]);
  useEffect(() => setPage(0), [kind, debouncedSearch]);

  const metrics = useMemo(() => {
    const summary = resource.data?.summary;
    const severities = summary?.alarm_events_by_severity || {};
    return {
      severe: (severities.critical || 0) + (severities.high || 0),
      nuisance: (severities.info || 0) + (severities.low || 0) + (severities.medium || 0),
      openIncidents:
        (summary?.incident_count_by_status?.open || 0) +
        (summary?.incident_count_by_status?.investigating || 0),
      actions: Object.values(
        summary?.corrective_action_count_by_status || {},
      ).reduce((total, count) => total + count, 0),
    };
  }, [resource.data]);
  const traceability = resource.data?.summary.incident_traceability;

  return (
    <>
      <PageHeading
        eyebrow="Measured outputs"
        title="Analytics"
        description="Review recurrence, high-volume alarm bursts, nuisance clusters, severity mix, and workflow outcomes."
      />
      {resource.loading && !resource.data && <LoadingState />}
      {resource.error && <ErrorState error={resource.error} />}
      {resource.data && (
        <>
          <div className="metric-grid">
            <MetricCard
              label="Alarm events"
              value={resource.data.summary.alarm_event_count}
            />
            <MetricCard
              label="Severe events"
              value={metrics.severe}
              note="High and critical"
              accent="red"
            />
            <MetricCard
              label="Nuisance-severity events"
              value={metrics.nuisance}
              note="Info, low, or medium"
              accent="amber"
            />
            <MetricCard
              label="Late acknowledgements"
              value={resource.data.summary.late_acknowledgement_count}
              accent="violet"
            />
            <MetricCard
              label="Mean acknowledgement response"
              value={formatSeconds(
                resource.data.summary.mean_acknowledgement_response_seconds,
              )}
              note={`${resource.data.summary.acknowledgement_response_observation_count} valid response times`}
              accent="violet"
            />
            <MetricCard
              label="Acknowledgement SLA exceptions"
              value={resource.data.summary.acknowledgement_sla_exception_count}
              note={`${resource.data.summary.late_acknowledgement_count} late · ${resource.data.summary.acknowledgement_before_alarm_count} before alarm · ${resource.data.summary.unacknowledged_required_alarm_event_count} required but missing`}
              accent="red"
            />
            <MetricCard
              label="Recurring clusters"
              value={resource.data.summary.repeated_alarm_cluster_count}
              note="3+ events, gaps ≤ 1 minute"
              accent="teal"
            />
            <MetricCard
              label="Severe clusters"
              value={resource.data.summary.alarm_cluster_count_by_kind.severe}
              note="High and critical"
              accent="red"
            />
            <MetricCard
              label="Nuisance clusters"
              value={resource.data.summary.alarm_cluster_count_by_kind.nuisance}
              note="Info, low, and medium"
              accent="amber"
            />
            <MetricCard
              label="Verified corrective actions"
              value={resource.data.summary.verified_corrective_action_count}
              note={`${resource.data.summary.closed_unverified_corrective_action_count} closed without passed verification`}
              accent="green"
            />
            <MetricCard
              label="Traceable incidents"
              value={traceability.traceable_incident_count}
              note={`${traceability.incident_count} total incidents`}
              accent="teal"
            />
            <MetricCard
              label="Incident traceability exceptions"
              value={traceability.traceability_exception_incident_count}
              note={`${traceability.missing_alarm_context_incident_count} missing alarms · ${traceability.missing_machine_state_context_incident_count} missing state · ${traceability.missing_lifecycle_history_incident_count} missing incident history · ${traceability.missing_action_lifecycle_history_incident_count} missing action history`}
              accent="red"
            />
            <MetricCard
              label="Open incidents"
              value={metrics.openIncidents}
              note={`${metrics.actions} corrective actions`}
              accent="green"
            />
          </div>
          <Panel
            title="Alarm floods and recurring patterns"
            description="Clusters group the same alarm on the same machine when consecutive events are no more than one minute apart; event counts make high-volume bursts visible."
          >
            <div className="toolbar">
              <SearchField
                value={search}
                onChange={setSearch}
                label="Search machine or alarm code"
              />
              <FilterField label="Pattern type">
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                >
                  <option value="">All recurring patterns</option>
                  <option value="severe">High / critical clusters</option>
                  <option value="nuisance">Info / low / medium nuisance clusters</option>
                </select>
              </FilterField>
            </div>
            {resource.data.clusters.items.length ? (
              <div className="table-wrap">
                <table>
                  <caption className="sr-only">Recurring alarm clusters</caption>
                  <thead>
                    <tr>
                      <th>Pattern</th>
                      <th>Machine</th>
                      <th>Severity</th>
                      <th>Events</th>
                      <th>Window</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resource.data.clusters.items.map((cluster, index) => (
                      <tr
                        key={`${cluster.machine_id}-${cluster.alarm_definition_id}-${cluster.starts_at}-${index}`}
                      >
                        <td>
                          <strong>{cluster.alarm_name}</strong>
                          <small>{cluster.alarm_code}</small>
                        </td>
                        <td>{cluster.machine_code}</td>
                        <td><SeverityBadge value={cluster.severity} /></td>
                        <td>
                          <strong>{cluster.event_count}</strong>
                        </td>
                        <td>
                          <time dateTime={cluster.starts_at}>
                            {formatTime(cluster.starts_at)}
                          </time>
                          <small>to {formatTime(cluster.ends_at)}</small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState>No recurring clusters match these filters.</EmptyState>
            )}
            <Pagination
              page={page}
              total={resource.data.clusters.total}
              limit={resource.data.clusters.limit}
              onChange={setPage}
              disabled={resource.loading}
            />
          </Panel>
          <div className="content-grid">
            <Panel title="Incidents by lifecycle status">
              <StatusBreakdown values={resource.data.summary.incident_count_by_status} />
            </Panel>
            <Panel title="Corrective actions by lifecycle status">
              <StatusBreakdown
                values={resource.data.summary.corrective_action_count_by_status}
              />
            </Panel>
          </div>
        </>
      )}
    </>
  );
}

function StatusBreakdown({ values = {} }) {
  const entries = Object.entries(values);
  if (!entries.length) return <EmptyState>No records yet.</EmptyState>;
  return (
    <ul className="breakdown-list">
      {entries.map(([status, count]) => (
        <li key={status}>
          <StatusBadge value={status} />
          <strong>{count.toLocaleString()}</strong>
        </li>
      ))}
    </ul>
  );
}

function App() {
  const [view, setView] = useState("overview");
  return (
    <div className="app-shell">
      <header className="topbar">
        <a
          href="#main"
          className="skip-link"
        >
          Skip to content
        </a>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">SF</div>
          <div>
            <strong>Safety Workflow</strong>
            <span>Industrial operations</span>
          </div>
        </div>
        <div className="topbar-meta">
          <span>Safety workflow · local API</span>
        </div>
      </header>
      <nav className="primary-nav" aria-label="Safety workflow sections">
        {NAV_ITEMS.map(([id, label]) => (
          <button
            key={id}
            className={view === id ? "nav-item nav-item-active" : "nav-item"}
            aria-current={view === id ? "page" : undefined}
            onClick={() => setView(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <main className="main-content" id="main">
        {view === "overview" && <OverviewView onNavigate={setView} />}
        {view === "machines" && <MachinesView onNavigate={setView} />}
        {view === "alarms" && <AlarmEventsView onNavigate={setView} />}
        {view === "acknowledgements" && <AcknowledgementsView />}
        {view === "incidents" && <IncidentsView />}
        {view === "actions" && <CorrectiveActionsView />}
        {view === "analytics" && <AnalyticsView />}
      </main>
      <footer className="app-footer">
        <span>Industrial Safety Workflow</span>
        <span>Event history remains append-only</span>
      </footer>
    </div>
  );
}

export default App;
