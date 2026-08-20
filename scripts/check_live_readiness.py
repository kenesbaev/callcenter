from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _present(environment: Mapping[str, str], name: str) -> bool:
    return bool(environment.get(name, "").strip())


def _csv(environment: Mapping[str, str], name: str) -> list[str]:
    return [
        item.strip() for item in environment.get(name, "").split(",") if item.strip()
    ]


def _safe_provider_ranges(environment: Mapping[str, str]) -> bool:
    values = _csv(environment, "SIP_ALLOWED_IPS")
    if not values:
        return False
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            # Local Docker test profiles intentionally use a service hostname.
            if environment.get("APP_ENV") not in {"development", "test"}:
                return False
            continue
        if network.prefixlen == 0:
            return False
    return True


def readiness(environment: Mapping[str, str]) -> dict[str, object]:
    architecture = environment.get("TELEPHONY_SIP_ARCHITECTURE", "direct")
    auth_mode = environment.get("SIP_AUTH_MODE", "")
    registration_auth = auth_mode == "registration" and all(
        _present(environment, name)
        for name in ("SIP_AUTH_USERNAME", "SIP_AUTH_PASSWORD")
    )
    ip_auth = auth_mode == "ip" and _safe_provider_ranges(environment)
    signaling_address = environment.get(
        "ASTERISK_EXTERNAL_SIGNALING_ADDRESS"
    ) or environment.get("ASTERISK_PUBLIC_ADDRESS", "")
    media_address = environment.get(
        "ASTERISK_EXTERNAL_MEDIA_ADDRESS"
    ) or environment.get("ASTERISK_PUBLIC_ADDRESS", "")
    edge_ready = architecture == "direct" or all(
        (
            environment.get("TELEPHONY_EDGE_TUNNEL_ENABLED") == "true",
            _present(environment, "TELEPHONY_EDGE_TUNNEL_CIDR"),
            _present(environment, "TELEPHONY_EDGE_PLATFORM_API_URL"),
        )
    )
    live_numbers = _csv(environment, "TELEPHONY_LIVE_TEST_NUMBERS")
    sip_ready = all(
        (
            architecture in {"direct", "uz_edge"},
            edge_ready,
            _present(environment, "SIP_PROVIDER_HOST"),
            _present(environment, "SIP_PROVIDER_PORT"),
            environment.get("SIP_PROVIDER_TRANSPORT") in {"udp", "tcp", "tls"},
            _present(environment, "SIP_DID"),
            _safe_provider_ranges(environment),
            registration_auth or ip_auth,
            _present(environment, "SIP_CODECS"),
            _present(environment, "SIP_MAX_CHANNELS"),
            bool(signaling_address),
            bool(media_address),
            bool(live_numbers)
            and all(E164.fullmatch(number) for number in live_numbers),
            environment.get("TELEPHONY_LIVE_DIAGNOSTIC_ENABLED") == "true",
        )
    )
    app_url = urlparse(environment.get("APP_BASE_URL", ""))
    webrtc_url = urlparse(environment.get("OPERATOR_WEBRTC_WSS_URL", ""))
    webrtc_ready = (
        app_url.scheme == "https"
        and bool(app_url.hostname)
        and webrtc_url.scheme == "wss"
        and bool(webrtc_url.hostname)
        and webrtc_url.hostname not in {"localhost", "127.0.0.1"}
    )
    model = environment.get("OPENAI_REALTIME_MODEL", "")
    openai_ready = all(
        (
            _present(environment, "OPENAI_API_KEY"),
            model == "gpt-realtime-2.1-mini",
            model in _csv(environment, "OPENAI_REALTIME_MODEL_ALLOWLIST"),
            environment.get("OPENAI_LIVE_TEST_APPROVAL")
            == "I_APPROVE_OPENAI_TEST_SPEND",
            _present(environment, "OPENAI_LIVE_TEST_AUDIO_PATH"),
        )
    )
    return {
        "sip": {
            "architecture": architecture,
            "credentials_present": registration_auth or ip_auth,
            "provider_allowlist_safe": _safe_provider_ranges(environment),
            "edge_tunnel_configured": edge_ready if architecture == "uz_edge" else None,
            "status": "ready_for_authorized_live_test" if sip_ready else "not_ready",
        },
        "openai": {
            "api_key_present": _present(environment, "OPENAI_API_KEY"),
            "model": model or None,
            "spend_approval_present": (
                environment.get("OPENAI_LIVE_TEST_APPROVAL")
                == "I_APPROVE_OPENAI_TEST_SPEND"
            ),
            "status": "ready_for_authorized_live_test" if openai_ready else "not_ready",
        },
        "browser_webrtc": {
            "https_configured": app_url.scheme == "https",
            "wss_configured": webrtc_url.scheme == "wss",
            "status": (
                "configuration_ready_live_verification_required"
                if webrtc_ready
                else "not_ready"
            ),
        },
    }


if __name__ == "__main__":
    # The report deliberately exposes only booleans, modes and safe statuses.
    # It never prints credentials, full DIDs, test numbers, keys or URLs.
    print(json.dumps(readiness(os.environ), indent=2, sort_keys=True))
