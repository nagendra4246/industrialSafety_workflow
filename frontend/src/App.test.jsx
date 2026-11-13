import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import App from "./App.jsx";

const machine = {
  id: "machine-1",
  plant_id: "plant-1",
  machine_code: "M-01",
  name: "Press line",
  status: "active",
  effective_from: "2026-10-01T00:00:00Z",
  effective_to: null,
};
const definition = {
  id: "definition-1",
  machine_id: machine.id,
  alarm_code: "PRESSURE-HIGH",
  name: "High pressure",
  severity: "critical",
  acknowledgement_required: true,
  ack_sla_seconds: 300,
  status: "active",
};
const alarm = {
  id: "alarm-event-1",
  machine_id: machine.id,
  alarm_definition_id: definition.id,
  external_event_id: "EVT-20261113-000001",
  event_at: "2026-11-13T12:00:00Z",
  received_at: "2026-11-13T12:00:03Z",
  status: "active",
};
const incident = {
  id: "incident-1",
  business_id: "INC-2026-000001",
  machine_id: machine.id,
  opened_at: "2026-11-13T12:00:01Z",
  status: "investigating",
  issue_type: "unsafe_condition",
};
const stateEvent = {
  id: "state-1",
  machine_id: machine.id,
  state: "fault",
  event_at: "2026-11-13T11:59:30Z",
  source_event_id: "STATE-001",
};
const action = {
  id: "action-1",
  business_id: "ACT-INC-2026-000001-01",
  incident_id: incident.id,
  owner: "maintenance-team",
  due_at: "2026-11-15T00:00:00Z",
  status: "completed",
  completed_at: "2026-11-13T13:00:00Z",
  verification_result: "not_started",
  verification_evidence: null,
};
const summary = {
  alarm_event_count: 23,
  alarm_events_by_severity: {
    critical: 2,
    high: 3,
    medium: 8,
    low: 7,
    info: 3,
  },
  severe_alarm_event_count: 5,
  nuisance_alarm_event_count: 18,
  acknowledgement_count: 10,
  timely_acknowledgement_count: 8,
  late_acknowledgement_count: 1,
  excused_acknowledgement_count: 0,
  acknowledgement_before_alarm_count: 1,
  unacknowledged_required_alarm_event_count: 1,
  acknowledgement_sla_exception_count: 3,
  mean_acknowledgement_response_seconds: 300.5,
  acknowledgement_response_observation_count: 9,
  incident_count_by_status: { open: 1, investigating: 1 },
  corrective_action_count_by_status: { completed: 1, closed: 3 },
  verified_corrective_action_count: 2,
  closed_unverified_corrective_action_count: 1,
  repeated_alarm_cluster_count: 2,
  alarm_cluster_count_by_kind: { nuisance: 1, severe: 1 },
  incident_traceability: {
    incident_count: 2,
    traceable_incident_count: 1,
    traceability_exception_incident_count: 1,
    missing_alarm_context_incident_count: 1,
    missing_machine_state_context_incident_count: 1,
    missing_lifecycle_history_incident_count: 0,
    missing_action_lifecycle_history_incident_count: 0,
    closed_unverified_action_incident_count: 1,
  },
};

function page(items, limit = 20, offset = 0) {
  return { items, total: items.length, limit, offset };
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    text: async () => JSON.stringify(payload),
  };
}

function makeFetch() {
  let verified = false;
  return vi.fn(async (input, options = {}) => {
    const url = new URL(input, "http://localhost");
    const path = url.pathname;
    const method = options.method || "GET";
    if (path.endsWith("/analytics/summary")) {
      return jsonResponse(summary);
    }
    if (path.endsWith("/analytics/alarm-clusters")) {
      return jsonResponse(page([]));
    }
    if (path.endsWith("/alarm-definitions")) {
      return jsonResponse(page([definition], 200));
    }
    if (path.endsWith("/machines")) {
      return jsonResponse(page([machine], Number(url.searchParams.get("limit")) || 20));
    }
    if (path.endsWith("/plants")) {
      return jsonResponse(
        page(
          [{
            id: "plant-1",
            plant_code: "SAFE-01",
            name: "North plant",
            status: "active",
          }],
          Number(url.searchParams.get("limit")) || 20,
        ),
      );
    }
    if (path.endsWith("/operator-shifts")) {
      const now = Date.now();
      return jsonResponse(
        page(
          [{
            id: "shift-1",
            shift_code: "SHIFT-01",
            operator_code: "OP-01",
            starts_at: new Date(now - 60 * 60 * 1000).toISOString(),
            ends_at: new Date(now + 60 * 60 * 1000).toISOString(),
          }],
          Number(url.searchParams.get("limit")) || 20,
        ),
      );
    }
    if (path.endsWith("/alarm-events")) {
      if (url.searchParams.get("severity") === "high") {
        return jsonResponse(page([]));
      }
      return jsonResponse(
        page([alarm], Number(url.searchParams.get("limit")) || 20),
      );
    }
    if (path.endsWith("/incidents")) {
      if (url.searchParams.get("status") === "open") {
        return jsonResponse(page([]));
      }
      return jsonResponse(
        page([incident], Number(url.searchParams.get("limit")) || 20),
      );
    }
    if (path.endsWith("/corrective-actions")) {
      return jsonResponse(
        page([action], Number(url.searchParams.get("limit")) || 20),
      );
    }
    if (path.endsWith("/acknowledgements")) {
      return jsonResponse(page([]));
    }
    if (path.endsWith("/state-events")) {
      return jsonResponse(page([stateEvent]));
    }
    if (path.endsWith("/evidence")) {
      return jsonResponse({
        incident,
        alarm_events: [alarm],
        machine_state_events: [stateEvent],
        corrective_actions: [
          verified
            ? {
                ...action,
                status: "closed",
                verification_result: "passed",
                verification_evidence: "Guard interlock tested.",
              }
            : action,
        ],
        lifecycle_history: [
          {
            id: "history-1",
            transition: "opened",
            occurred_at: incident.opened_at,
            actor_reference: "safety_rule",
          },
        ],
        traceability: {
          traceable: true,
          source_alarm_event_count: 1,
          machine_state_event_count: 1,
          corrective_action_count: 1,
          lifecycle_record_count: 6,
          action_lifecycle_record_count: 5,
          closed_unverified_action_count: 0,
          exceptions: [],
        },
      });
    }
    if (method === "POST" && path.endsWith("/acknowledgements")) {
      return jsonResponse({ acknowledgement: {}, idempotent: false });
    }
    if (method === "POST" && path.endsWith("/verify")) {
      verified = true;
      return jsonResponse({
        action: {
          ...action,
          status: "closed",
          verification_result: "passed",
          verification_evidence: "Guard interlock tested.",
        },
        idempotent: false,
      });
    }
    if (method === "PATCH") {
      return jsonResponse({ ...incident, status: "resolved" });
    }
    return jsonResponse({ detail: `Unhandled mock route: ${path}` }, 404);
  });
}

describe("safety workflow UI", () => {
  it("shows overview metrics and visibly prioritizes severe alarms", async () => {
    vi.stubGlobal("fetch", makeFetch());
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Safety overview" }),
    ).toBeTruthy();
    expect(screen.getByText("High / critical")).toBeTruthy();
    expect(screen.getByText("High pressure")).toBeTruthy();
    expect(screen.getAllByText("Critical").length).toBeGreaterThan(0);
    expect(screen.getByText("Recurring clusters")).toBeTruthy();
    expect(
      within(screen.getByText("Verified actions").closest("article")).getByText(
        "2",
      ),
    ).toBeTruthy();
  });

  it("surfaces SLA, verification, cluster, and traceability metrics", async () => {
    vi.stubGlobal("fetch", makeFetch());
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Analytics" }));

    expect(
      await screen.findByRole("heading", { name: "Analytics" }),
    ).toBeTruthy();
    expect(screen.getByText("300.5 s")).toBeTruthy();
    expect(screen.getByText("Acknowledgement SLA exceptions")).toBeTruthy();
    expect(screen.getByText("Verified corrective actions")).toBeTruthy();
    expect(screen.getByText("Severe clusters")).toBeTruthy();
    expect(screen.getByText("Nuisance clusters")).toBeTruthy();
    expect(screen.getByText("Incident traceability exceptions")).toBeTruthy();
    expect(
      screen.getByText(
        "1 missing alarms · 1 missing state · 0 missing incident history · 0 missing action history",
      ),
    ).toBeTruthy();
  });

  it("debounces machine search and sends the filter to the server", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Machines" }));

    const search = await screen.findByRole("searchbox", {
      name: "Search machines",
    });
    await user.type(search, "Press");
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("search=Press"),
        ),
      ).toBe(true);
    });
    expect(await screen.findByText("Press line")).toBeTruthy();
  });

  it("records an acknowledgement against an active operator shift", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Alarm events" }));
    await user.click(await screen.findByRole("button", { name: "Details" }));
    await user.click(
      await screen.findByRole("button", { name: "Record acknowledgement" }),
    );
    await user.selectOptions(
      await screen.findByLabelText("Operator shift"),
      "shift-1",
    );
    await user.click(
      screen.getByRole("button", { name: "Save acknowledgement" }),
    );

    await waitFor(() => {
      const acknowledgementCall = fetchMock.mock.calls.find(
        ([input, options]) =>
          String(input).includes("/acknowledgements") &&
          options?.method === "POST",
      );
      expect(acknowledgementCall).toBeTruthy();
      const request = JSON.parse(acknowledgementCall[1].body);
      expect(request.alarm_event_id).toBe(alarm.id);
      expect(request.operator_shift_id).toBe("shift-1");
      expect(request.business_id).toBe(`ACK-${alarm.external_event_id}-01`);
    });
  });

  it("shows incident evidence and verifies a completed corrective action", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Incidents" }));
    await user.click(await screen.findByRole("button", { name: "Open detail" }));
    expect(await screen.findByText("Machine-state evidence")).toBeTruthy();
    expect(await screen.findByText("High pressure")).toBeTruthy();
    expect(
      screen.getByText(
        "Source alarm, machine-state, and lifecycle evidence is linked.",
      ),
    ).toBeTruthy();

    await user.click(
      screen.getByText("ACT-INC-2026-000001-01"),
    );
    const evidence = await screen.findByLabelText("Verification evidence");
    await user.type(evidence, "Guard interlock tested.");
    await user.click(screen.getByRole("button", { name: "Verify and close" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input, options]) =>
            String(input).endsWith("/corrective-actions/action-1/verify") &&
            options?.method === "POST",
        ),
      ).toBe(true);
    });
    expect(
      (await screen.findAllByText("Passed", { exact: true })).length,
    ).toBeGreaterThan(0);
  });
});
