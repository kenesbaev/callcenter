from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_readiness():
    script = Path(__file__).parents[3] / "scripts" / "check_live_readiness.py"
    spec = importlib.util.spec_from_file_location("check_live_readiness", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.readiness


def test_readiness_report_is_fail_closed_and_contains_no_secrets() -> None:
    readiness = _load_readiness()
    report = readiness(
        {
            "APP_ENV": "production",
            "TELEPHONY_SIP_ARCHITECTURE": "uz_edge",
            "SIP_AUTH_MODE": "registration",
            "SIP_AUTH_USERNAME": "user-secret",
            "SIP_AUTH_PASSWORD": "password-secret",
            "SIP_ALLOWED_IPS": "0.0.0.0/0",
            "OPENAI_API_KEY": "api-key-secret",
            "OPENAI_REALTIME_MODEL": "gpt-realtime-2.1-mini",
        }
    )
    assert report["sip"]["status"] == "not_ready"
    assert report["openai"]["status"] == "not_ready"
    serialized = repr(report)
    assert "password-secret" not in serialized
    assert "api-key-secret" not in serialized
    assert "user-secret" not in serialized


def test_readiness_accepts_complete_edge_configuration_without_exposing_values() -> None:
    readiness = _load_readiness()
    report = readiness(
        {
            "APP_ENV": "production",
            "APP_BASE_URL": "https://calls.example.test",
            "OPERATOR_WEBRTC_WSS_URL": "wss://calls.example.test/sip-ws",
            "TELEPHONY_SIP_ARCHITECTURE": "uz_edge",
            "TELEPHONY_EDGE_TUNNEL_ENABLED": "true",
            "TELEPHONY_EDGE_TUNNEL_CIDR": "10.77.0.0/24",
            "TELEPHONY_EDGE_PLATFORM_API_URL": "https://10.77.0.1/internal",
            "SIP_PROVIDER_HOST": "sip.example.test",
            "SIP_PROVIDER_PORT": "5061",
            "SIP_PROVIDER_TRANSPORT": "tls",
            "SIP_DID": "+998000000000",
            "SIP_ALLOWED_IPS": "203.0.113.10/32",
            "SIP_AUTH_MODE": "registration",
            "SIP_AUTH_USERNAME": "test-user",
            "SIP_AUTH_PASSWORD": "test-password",
            "SIP_CODECS": "ulaw",
            "SIP_MAX_CHANNELS": "1",
            "ASTERISK_EXTERNAL_SIGNALING_ADDRESS": "203.0.113.20",
            "ASTERISK_EXTERNAL_MEDIA_ADDRESS": "203.0.113.20",
            "TELEPHONY_LIVE_TEST_NUMBERS": "+998000000001",
            "TELEPHONY_LIVE_DIAGNOSTIC_ENABLED": "true",
        }
    )
    assert report["sip"]["status"] == "ready_for_authorized_live_test"
    assert report["browser_webrtc"]["status"] == ("configuration_ready_live_verification_required")
