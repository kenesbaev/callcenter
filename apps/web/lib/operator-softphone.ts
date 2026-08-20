import {
  Registerer,
  RegistererState,
  SessionState,
  UserAgent,
  Web,
} from "sip.js";
import type { Invitation } from "sip.js";
import type { OperatorWebRtcConfiguration } from "@/lib/types";

export type SoftphoneState =
  | "idle"
  | "registering"
  | "registered"
  | "ringing"
  | "active"
  | "on_hold"
  | "disconnected"
  | "failed";

type StateListener = (state: SoftphoneState, error?: string) => void;

export type OperatorAudioDevice = {
  deviceId: string;
  kind: "audioinput" | "audiooutput";
  label: string;
};

export type OperatorAudioPreferences = {
  microphoneId?: string;
  speakerId?: string;
};

export class OperatorSoftphone {
  private userAgent?: UserAgent;
  private registerer?: Registerer;
  private invitation?: Invitation;
  private remoteAudio?: HTMLAudioElement;
  private listener?: StateListener;
  private autoAnswer = false;
  private state: SoftphoneState = "idle";
  private microphoneDeviceId?: string;
  private speakerDeviceId?: string;

  onState(listener: StateListener) {
    this.listener = listener;
    listener(this.state);
    return () => {
      if (this.listener === listener) this.listener = undefined;
    };
  }

  currentState() {
    return this.state;
  }

  async listAudioDevices(): Promise<OperatorAudioDevice[]> {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter(
        (
          device,
        ): device is MediaDeviceInfo & {
          kind: "audioinput" | "audiooutput";
        } => device.kind === "audioinput" || device.kind === "audiooutput",
      )
      .map((device, index) => ({
        deviceId: device.deviceId,
        kind: device.kind,
        label:
          device.label ||
          `${device.kind === "audioinput" ? "Микрофон" : "Динамик"} ${index + 1}`,
      }));
  }

  async register(
    configuration: OperatorWebRtcConfiguration,
    preferences: OperatorAudioPreferences = {},
  ): Promise<void> {
    if (this.userAgent) await this.stop();
    const uri = UserAgent.makeURI(configuration.sip_uri);
    if (!uri) throw new Error("Некорректный SIP URI оператора");
    const username = uri.user;
    if (!username) throw new Error("В SIP URI отсутствует имя оператора");
    this.setState("registering");
    this.microphoneDeviceId = preferences.microphoneId;
    this.speakerDeviceId = preferences.speakerId;
    const audioConstraint: boolean | MediaTrackConstraints =
      preferences.microphoneId
        ? { deviceId: { exact: preferences.microphoneId } }
        : true;
    const userAgent = new UserAgent({
      uri,
      authorizationUsername: username,
      authorizationPassword: configuration.authorization,
      transportOptions: {
        server: configuration.websocket_url,
        connectionTimeout: 8,
      },
      reconnectionAttempts: 3,
      reconnectionDelay: 4,
      sessionDescriptionHandlerFactoryOptions: {
        constraints: { audio: audioConstraint, video: false },
      },
      logBuiltinEnabled: false,
      delegate: {
        onInvite: (invitation) => {
          this.invitation = invitation;
          this.listenToSession(invitation);
          this.setState("ringing");
          if (this.autoAnswer) void this.answer();
        },
      },
    });
    this.userAgent = userAgent;
    const registerer = new Registerer(userAgent, { expires: 60 });
    this.registerer = registerer;
    await userAgent.start();
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        cleanup();
        reject(new Error("Истекло время регистрации WebRTC-софтфона"));
      }, 8_000);
      const cleanup = () => {
        window.clearTimeout(timeout);
        registerer.stateChange.removeListener(onState);
      };
      const onState = (state: RegistererState) => {
        if (state === RegistererState.Registered) {
          cleanup();
          this.setState("registered");
          resolve();
        } else if (state === RegistererState.Terminated) {
          cleanup();
          reject(new Error("Регистрация WebRTC-софтфона отклонена"));
        }
      };
      registerer.stateChange.addListener(onState);
      void registerer.register().catch((error: unknown) => {
        cleanup();
        reject(error);
      });
    });
  }

  armAutoAnswer() {
    this.autoAnswer = true;
    if (this.invitation?.state === SessionState.Establishing)
      void this.answer();
  }

  async answer(): Promise<void> {
    const invitation = this.invitation;
    if (!invitation || invitation.state !== SessionState.Establishing) return;
    await invitation.accept({
      sessionDescriptionHandlerOptions: {
        constraints: {
          audio: this.microphoneDeviceId
            ? { deviceId: { exact: this.microphoneDeviceId } }
            : true,
          video: false,
        },
      },
    });
  }

  setMuted(muted: boolean) {
    const peer = this.sessionDescriptionHandler()?.peerConnection;
    for (const sender of peer?.getSenders() ?? []) {
      if (sender.track?.kind === "audio") sender.track.enabled = !muted;
    }
  }

  async hold(): Promise<void> {
    if (this.invitation?.state !== SessionState.Established) return;
    await this.invitation.invite({
      sessionDescriptionHandlerModifiers: [Web.holdModifier],
    });
    this.setState("on_hold");
  }

  async resume(): Promise<void> {
    if (this.invitation?.state !== SessionState.Established) return;
    await this.invitation.invite({ sessionDescriptionHandlerModifiers: [] });
    this.setState("active");
  }

  async hangup(): Promise<void> {
    if (!this.invitation) return;
    if (this.invitation.state === SessionState.Established)
      await this.invitation.bye();
    else if (this.invitation.state === SessionState.Establishing)
      await this.invitation.reject();
  }

  async stop(): Promise<void> {
    this.autoAnswer = false;
    if (this.registerer?.state === RegistererState.Registered) {
      await this.registerer.unregister().catch(() => undefined);
    }
    await this.userAgent?.stop().catch(() => undefined);
    this.userAgent = undefined;
    this.registerer = undefined;
    this.invitation = undefined;
    this.remoteAudio?.remove();
    this.remoteAudio = undefined;
    this.microphoneDeviceId = undefined;
    this.speakerDeviceId = undefined;
    this.setState("disconnected");
  }

  private listenToSession(invitation: Invitation) {
    invitation.stateChange.addListener((state) => {
      if (state === SessionState.Established) {
        this.attachRemoteAudio();
        this.setState("active");
      } else if (state === SessionState.Terminated) {
        this.invitation = undefined;
        this.setState("registered");
      }
    });
  }

  private sessionDescriptionHandler():
    Web.SessionDescriptionHandler | undefined {
    const handler = this.invitation?.sessionDescriptionHandler;
    return handler instanceof Web.SessionDescriptionHandler
      ? handler
      : undefined;
  }

  private attachRemoteAudio() {
    const peer = this.sessionDescriptionHandler()?.peerConnection;
    if (!peer) return;
    const stream = new MediaStream();
    for (const receiver of peer.getReceivers()) {
      if (receiver.track.kind === "audio") stream.addTrack(receiver.track);
    }
    const audio = this.remoteAudio ?? document.createElement("audio");
    audio.autoplay = true;
    audio.srcObject = stream;
    audio.hidden = true;
    if (!this.remoteAudio) document.body.append(audio);
    this.remoteAudio = audio;
    if (this.speakerDeviceId && "setSinkId" in audio) {
      void audio
        .setSinkId(this.speakerDeviceId)
        .catch(() => this.setState("failed", "Не удалось выбрать динамик"));
    }
    void audio
      .play()
      .catch(() => this.setState("failed", "Браузер заблокировал звук"));
  }

  private setState(state: SoftphoneState, error?: string) {
    this.state = state;
    this.listener?.(state, error);
  }
}

let sharedSoftphone: OperatorSoftphone | undefined;

export function getOperatorSoftphone() {
  sharedSoftphone ??= new OperatorSoftphone();
  return sharedSoftphone;
}
