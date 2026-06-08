from __future__ import annotations

from agent_dev_board_api.app import (
    _cors_allowed_origin_regex,
    _discover_github_token_for_foundry,
    _github_client_id_from_authorization_url,
    _github_device_client_id_from_env,
    _normalize_local_agent_url,
    _normalize_url,
)


def test_cors_defaults_to_localhost_regex(monkeypatch) -> None:
    monkeypatch.delenv("CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGIN_REGEX", raising=False)

    assert _cors_allowed_origin_regex()


def test_normalize_url_rejects_non_http_schemes() -> None:
    assert _normalize_url("file:///etc/passwd") == ""
    assert _normalize_url("javascript:alert(1)") == ""


def test_normalize_url_defaults_remote_hosts_to_https() -> None:
    assert _normalize_url("foundry.example.com") == "https://foundry.example.com"


def test_normalize_url_blocks_remote_http_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP", raising=False)

    assert _normalize_url("http://foundry.example.com") == ""


def test_normalize_url_allows_remote_http_with_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP", "true")

    assert _normalize_url("http://foundry.example.com") == "http://foundry.example.com"


def test_local_agent_url_must_be_loopback_http() -> None:
    assert _normalize_local_agent_url("http://127.0.0.1:8085") == "http://127.0.0.1:8085"
    assert _normalize_local_agent_url("https://127.0.0.1:8085") == ""
    assert _normalize_local_agent_url("https://agent.example.com") == ""


def test_custom_foundry_requires_explicit_github_token() -> None:
    assert _discover_github_token_for_foundry("", "https://custom.example.com")[1] == (
        "custom_foundry_requires_explicit_token"
    )
    assert _discover_github_token_for_foundry("ghp_explicit", "https://custom.example.com") == (
        "ghp_explicit",
        "user_input",
    )


def test_github_client_id_from_authorization_url() -> None:
    client_id, scope = _github_client_id_from_authorization_url(
        "https://github.com/login/oauth/authorize?client_id=abc123&scope=read%3Auser"
    )

    assert client_id == "abc123"
    assert scope == "read:user"


def test_github_client_id_from_authorization_url_rejects_non_github() -> None:
    assert _github_client_id_from_authorization_url("https://example.com/login/oauth/authorize?client_id=abc") == (
        "",
        "",
    )


def test_github_device_client_id_from_env_origin_mapping(monkeypatch) -> None:
    monkeypatch.setenv(
        "CCFOUNDRY_GITHUB_DEVICE_CLIENT_IDS_JSON",
        '{"https://foundry.example.com":"origin-client","foundry.example.com":"host-client"}',
    )
    monkeypatch.delenv("CCFOUNDRY_GITHUB_DEVICE_CLIENT_ID", raising=False)

    assert _github_device_client_id_from_env("https://foundry.example.com") == "origin-client"


def test_github_device_client_id_from_env_default(monkeypatch) -> None:
    monkeypatch.setenv("CCFOUNDRY_GITHUB_DEVICE_CLIENT_IDS_JSON", "{}")
    monkeypatch.setenv("CCFOUNDRY_GITHUB_DEVICE_CLIENT_ID", "default-client")

    assert _github_device_client_id_from_env("https://custom.example.com") == "default-client"
