# Teamora Voice product design

## Product character

Teamora Voice should feel like an operations console: calm, precise and dense enough for supervisors. It avoids robot/brain imagery, decorative glass panels, invented customer logos and random production metrics.

The default theme is dark. Soft blue-violet glow is reserved for the landing hero, an active live call and the primary CTA. Tables, forms and audit surfaces rely on hierarchy, contrast and thin borders rather than gradients.

## Brand

The logo is a code-native SVG/CSS mark: an abstract `T` crossed by a short three-stroke sound wave, paired with the text `Teamora Voice`. A telephone handset is never the primary mark.

## Design tokens

| Token                   | Value     | Use                        |
| ----------------------- | --------- | -------------------------- |
| `--tv-background`       | `#070A12` | page background            |
| `--tv-surface`          | `#0D1320` | cards and navigation       |
| `--tv-surface-elevated` | `#121A2A` | menus and active cards     |
| `--tv-border`           | `#243047` | thin boundaries            |
| `--tv-primary`          | `#6D5DFB` | primary actions and focus  |
| `--tv-secondary`        | `#2D9CFF` | links and realtime signal  |
| `--tv-success`          | `#2DD4A8` | healthy/complete           |
| `--tv-warning`          | `#F5B942` | beta and attention         |
| `--tv-danger`           | `#FF5D6C` | destructive/error          |
| `--tv-text-primary`     | `#F7F9FC` | primary text               |
| `--tv-text-secondary`   | `#B5BED0` | labels and supporting text |
| `--tv-text-muted`       | `#7E8AA4` | metadata                   |

Geometry: sidebar `240px`, collapsed sidebar `78px`, topbar `64px`, card radius `14px`, control radius `12px`, 1px borders. Minimum interactive target is `40px`; focus rings are always visible.

## Information architecture

Primary tenant navigation:

1. Overview
2. Live calls
3. AI operators
4. Call flows
5. Knowledge
6. Language Lab
7. Conversations
8. Human team and queues
9. Customers
10. Phone and SIP
11. Integrations
12. Analytics and usage
13. Billing
14. Settings and audit

Platform navigation is a visually distinct `/platform` area and is never shown to tenant-only roles.

## Route state contract

Every route has loading, empty, content, error and forbidden states. An unavailable integration shows its provider, expected capability and reason (`Credentials missing`, `Implementation in development`, or `Verification required`) without an enabled connect/test button.

The following badges have fixed semantics:

- `Live verified`: real provider evidence exists.
- `Configured`: credentials/config exist, behavior not yet proven.
- `Development`: development-only implementation.
- `Coming later`: no executable backend.
- `Beta`: supported with a visible quality caveat (`uz`).
- `Experimental`: feature-flagged and not production-ready (`kaa`).

## Key screens

### Landing

The hero uses “AI Call Center that speaks your customer’s language”, localized into Russian through the locale dictionary. `Start test call` opens the development simulator only when enabled; otherwise it links to registration with an accurate label. No fake social proof.

### Dashboard

KPI cards contain values from the API and a selected time range. Empty production tenants show zeros and onboarding guidance, not demo values. Development seed data carries a visible `Demo data` badge. Recharts plots call outcomes, languages and volume; tables cover topics and operator load.

### Live call

A compact three-column layout shows call identity/state, realtime transcript and tool/transfer timeline. Transfer is permission-gated. End-call requires confirmation. Supervisor listen controls are absent until a dedicated permission, legal configuration and audit flow exist.

### AI operator wizard

An 11-step accessible wizard creates a draft version. Publish is a separate confirmation and makes the version immutable. Active calls retain their pinned version. Test call is explicitly a simulation unless a live number/provider is verified.

### Language Lab

Audio upload includes type/size validation and a privacy warning. Provider comparison results show provider, language, latency, transcript, manual correction, score and reviewer. TTS playback is signed/private. `kaa` always shows `Experimental`; `uz` shows `Beta` until quality policy is satisfied.

### Conversation detail

Header: status, channel, language, outcome, duration and verified recording availability. Body: summary, redacted customer context, segment timeline, tool executions and transfer details. Recording requests are short-lived and audited. Billing roles cannot open this page.

## Accessibility and responsive behavior

- WCAG AA contrast is a release gate; color is never the only status indicator.
- Keyboard order follows visual order; skip link, landmarks, field descriptions and live regions are required.
- Motion respects `prefers-reduced-motion`.
- At 1280px the sidebar is full width; below it collapses to 78px. Tablet layout stacks secondary panels and preserves tables through controlled horizontal scrolling.
- Skeletons mirror final geometry. Error panels retain correlation IDs without exposing stack traces.

## Data visualization rules

- Zero is shown as zero; unavailable data uses `—` with a tooltip.
- Estimated OpenAI cost is labeled “Estimate” and includes the pricing snapshot timestamp/config source.
- Percentages state numerator/denominator in accessible text.
- No randomized numbers outside fixtures explicitly labeled development-only.
