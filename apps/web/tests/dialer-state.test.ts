import { describe, expect, it } from "vitest";
import {
  callElapsedSeconds,
  newestCall,
  shouldHandleDialerShortcut,
} from "@/components/dialer-view";
import type { Call } from "@/lib/types";

function call(overrides: Partial<Call> = {}): Call {
  return {
    id: "call-1",
    project_id: "project-1",
    channel: "development_simulator",
    status: "active",
    language: "ru",
    customer_id: "customer-1",
    operator_user_id: "operator-1",
    ai_operator_id: null,
    call_flow_version_id: null,
    direction: "outbound",
    caller_type: "human_operator",
    provider: "mock",
    provider_call_id: "mock:call-1",
    provider_state: "active",
    recording_state: "stopped",
    from_number: "MOCK",
    to_number: "+998901234567",
    started_at: "2026-07-31T10:00:00.000Z",
    ringing_at: "2026-07-31T10:00:01.000Z",
    answered_at: "2026-07-31T10:00:05.000Z",
    held_at: null,
    ended_at: null,
    duration_seconds: 0,
    transfer_reason: null,
    hangup_cause: null,
    raw_provider_cause: null,
    last_provider_event_at: null,
    state_version: 3,
    is_demo: true,
    ...overrides,
  };
}

describe("dialer call timer", () => {
  it("starts at answered_at rather than started_at", () => {
    expect(
      callElapsedSeconds(
        call(),
        new Date("2026-07-31T10:00:15.000Z").getTime(),
      ),
    ).toBe(10);
  });

  it("freezes at ended_at for terminal calls", () => {
    expect(
      callElapsedSeconds(
        call({
          status: "completed",
          provider_state: "completed",
          ended_at: "2026-07-31T10:00:25.000Z",
          hangup_cause: "normal",
        }),
        new Date("2026-07-31T11:00:00.000Z").getTime(),
      ),
    ).toBe(20);
  });

  it("remains zero until a call is answered", () => {
    expect(callElapsedSeconds(call({ answered_at: null }))).toBe(0);
  });
});

describe("dialer polling state", () => {
  it("does not replace a newer state with an older response", () => {
    const current = call({ status: "active", state_version: 5 });
    const stale = call({ status: "ringing", state_version: 4 });
    expect(newestCall(current, stale)).toBe(current);
  });

  it("accepts an equal or newer state version", () => {
    const current = call({ status: "ringing", state_version: 2 });
    const incoming = call({ status: "active", state_version: 3 });
    expect(newestCall(current, incoming)).toBe(incoming);
  });
});

describe("dialer keyboard shortcuts", () => {
  it("does not trigger while the operator types or a modal is open", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const dialogChild = document.createElement("span");
    dialog.append(dialogChild);

    expect(shouldHandleDialerShortcut(input)).toBe(false);
    expect(shouldHandleDialerShortcut(textarea)).toBe(false);
    expect(shouldHandleDialerShortcut(dialogChild)).toBe(false);
  });

  it("allows shortcuts from the neutral workspace", () => {
    const workspace = document.createElement("div");
    expect(shouldHandleDialerShortcut(workspace)).toBe(true);
  });
});
