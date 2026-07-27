# Call flow

```mermaid
sequenceDiagram
    participant P as SIP provider
    participant A as Asterisk
    participant API as FastAPI control plane
    participant G as Voice Gateway
    participant R as Realtime provider
    participant H as Human queue
    P->>A: INVITE with called DID
    A->>API: Resolve route by normalized DID
    API-->>A: Tenant, published operator version, limits
    A->>G: ARI Stasis + External Media
    G->>API: Idempotently create Call
    G->>R: Released Realtime WebSocket session
    R-->>G: Session ready
    G-->>P: Virtual assistant and recording disclosure
    loop Conversation
        A->>G: Audio frame
        G->>R: Input audio
        R-->>G: Audio, transcript or typed tool request
        G->>API: Authorized tenant-scoped tool execution
        API-->>G: Minimal safe result
        G-->>A: Output audio
    end
    alt Handoff condition
        G->>API: TransferRequest with reason and context
        G->>A: Continue to approved queue
        A->>H: Queue transfer
    end
    G->>API: Final events and usage envelope
    API-->>G: Accepted idempotently
```

The greeting must identify a virtual assistant and state that the conversation may be recorded. Recording can be disabled by tenant policy and paused around sensitive data.

Handoff is requested on explicit human request, repeated misunderstanding, knowledge gap, prohibited operation, critical tool error, serious complaint, duration limit, or confidence below policy. The operator context contains customer identity when authorized, language, reason, partial summary, transcript, safe tool history and CRM reference. If no operator is available, the configured flow offers a callback, reports business hours and ends cleanly.

The development simulator exercises persistence and policy using text. It does not exercise SIP, RTP, audio codecs, STT, TTS or a Realtime model.
