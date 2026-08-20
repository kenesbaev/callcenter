# Uzbekistan telephony edge template

This directory is a non-deploying reference for the Stage 17 `uz_edge` topology.
It contains no keys, provider credentials, IP addresses or production firewall
commands. Generate WireGuard keys outside Git and substitute all angle-bracket
placeholders in the host secret/configuration system.

Run Asterisk and the media-facing Voice Gateway on the same private edge network.
Expose provider SIP/RTP only to confirmed provider ranges, expose browser WSS and
DTLS-SRTP only through a trusted TLS configuration, and route the Gateway's
authenticated FastAPI service traffic through WireGuard. Never publish ARI.
