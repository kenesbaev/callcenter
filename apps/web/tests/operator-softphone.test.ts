import { beforeEach, describe, expect, it, vi } from "vitest";

const sip = vi.hoisted(() => {
  class Emitter<T> {
    listeners = new Set<(value: T) => void>();
    addListener(listener: (value: T) => void) {
      this.listeners.add(listener);
    }
    removeListener(listener: (value: T) => void) {
      this.listeners.delete(listener);
    }
    emit(value: T) {
      for (const listener of this.listeners) listener(value);
    }
  }
  const registerState = new Emitter<string>();
  const sessionState = new Emitter<string>();
  const senderTrack = { kind: "audio", enabled: true };
  const receiverTrack = { kind: "audio" };
  const handler = {
    peerConnection: {
      getSenders: () => [{ track: senderTrack }],
      getReceivers: () => [{ track: receiverTrack }],
    },
  };
  return {
    registerState,
    sessionState,
    senderTrack,
    handler,
    userAgentOptions: undefined as Record<string, unknown> | undefined,
    register: vi.fn(async () => registerState.emit("Registered")),
    unregister: vi.fn(async () => undefined),
    start: vi.fn(async () => undefined),
    stop: vi.fn(async () => undefined),
    accept: vi.fn(async () => undefined),
    invite: vi.fn(async () => undefined),
    bye: vi.fn(async () => undefined),
    reject: vi.fn(async () => undefined),
  };
});

vi.mock("sip.js", () => {
  class SessionDescriptionHandler {
    peerConnection = sip.handler.peerConnection;
  }
  class UserAgent {
    static makeURI(value: string) {
      return { user: value.split(":")[1]?.split("@")[0] };
    }
    constructor(options: Record<string, unknown>) {
      sip.userAgentOptions = options;
    }
    start = sip.start;
    stop = sip.stop;
  }
  class Registerer {
    state = "Registered";
    stateChange = sip.registerState;
    register = sip.register;
    unregister = sip.unregister;
  }
  class Invitation {
    state = "Establishing";
    stateChange = sip.sessionState;
    sessionDescriptionHandler = new SessionDescriptionHandler();
    accept = sip.accept;
    invite = sip.invite;
    bye = sip.bye;
    reject = sip.reject;
  }
  return {
    Invitation,
    Registerer,
    RegistererState: { Registered: "Registered", Terminated: "Terminated" },
    SessionState: {
      Establishing: "Establishing",
      Established: "Established",
      Terminated: "Terminated",
    },
    UserAgent,
    Web: { SessionDescriptionHandler, holdModifier: vi.fn() },
  };
});

import { Invitation, SessionState } from "sip.js";
import { OperatorSoftphone } from "@/lib/operator-softphone";

const configuration = {
  websocket_url: "wss://call-center.example/sip-ws",
  sip_uri: "sip:operator-10000000-0000-4000-8000-000000000001@kline.invalid",
  authorization: "temporary-secret-that-is-never-persisted",
  expires_at: "2026-08-11T12:00:00Z",
  ice_servers: [],
  dtls_srtp_required: true,
  register_required: true,
  live_verification: "local",
};

describe("OperatorSoftphone", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sip.registerState.listeners.clear();
    sip.sessionState.listeners.clear();
    sip.senderTrack.enabled = true;
    vi.stubGlobal(
      "MediaStream",
      class {
        addTrack() {}
      },
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  });

  it("registers, answers, mutes, holds, resumes and hangs up", async () => {
    const softphone = new OperatorSoftphone();
    const states: string[] = [];
    softphone.onState((state) => states.push(state));
    await softphone.register(configuration, {
      microphoneId: "preferred-microphone",
      speakerId: "preferred-speaker",
    });
    expect(states).toContain("registered");
    expect(JSON.stringify(sip.userAgentOptions)).toContain(
      configuration.websocket_url,
    );
    expect(JSON.stringify(sip.userAgentOptions)).toContain(
      "preferred-microphone",
    );

    const InvitationFixture = Invitation as unknown as new () => Invitation;
    const invitation = new InvitationFixture();
    const options = sip.userAgentOptions as {
      delegate: { onInvite: (value: Invitation) => void };
    };
    options.delegate.onInvite(invitation);
    softphone.armAutoAnswer();
    await vi.waitFor(() => expect(sip.accept).toHaveBeenCalledOnce());
    Object.defineProperty(invitation, "state", {
      configurable: true,
      value: SessionState.Established,
    });
    sip.sessionState.emit(SessionState.Established);
    softphone.setMuted(true);
    expect(sip.senderTrack.enabled).toBe(false);

    await softphone.hold();
    await softphone.resume();
    await softphone.hangup();
    expect(sip.invite).toHaveBeenCalledTimes(2);
    expect(sip.bye).toHaveBeenCalledOnce();
    await softphone.stop();
    expect(sip.unregister).toHaveBeenCalledOnce();
    expect(sip.stop).toHaveBeenCalledOnce();
  });
});
