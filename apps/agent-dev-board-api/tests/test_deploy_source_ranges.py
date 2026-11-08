"""Firewall source ranges chosen by the one-click Dev Board deploy.sh.

The script is sourced (its main only runs when executed) with a stub `curl`, so
the public-IP detection, Cloud Shell handling and validation run without gcloud
or network access.
"""

import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy.sh"


def run_snippet(tmp_path, snippet, *, env=None, curl_output=None, curl_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "curl_calls"
    curl = bindir / "curl"
    lines = ["#!/usr/bin/env bash", f'echo "$*" >> {shlex.quote(str(calls))}']
    if curl_output is not None:
        lines.append(f"printf '%s\\n' {shlex.quote(curl_output)}")
    lines.append(f"exit {curl_rc}")
    curl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    curl.chmod(0o755)

    environ = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    environ.update(env or {})
    completed = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(DEPLOY))}\n{snippet}"],
        capture_output=True,
        text=True,
        env=environ,
        stdin=subprocess.DEVNULL,
    )
    curl_calls = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return completed, curl_calls


RESOLVE = 'resolve_source_ranges\necho "RANGES=$SOURCE_RANGES"'


def test_sourcing_does_not_run_main(tmp_path):
    completed, curl_calls = run_snippet(tmp_path, "echo sourced")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "sourced"
    assert "Deploying" not in completed.stdout
    assert curl_calls == []


def test_default_allows_only_detected_public_ip(tmp_path):
    completed, curl_calls = run_snippet(tmp_path, RESOLVE, curl_output="203.0.113.7")
    assert completed.returncode == 0, completed.stderr
    assert "RANGES=203.0.113.7/32" in completed.stdout
    assert "only to your public IP 203.0.113.7" in completed.stdout
    assert len(curl_calls) == 1


def test_detection_failure_requires_explicit_ranges(tmp_path):
    for output, rc in (("<html>blocked</html>", 0), (None, 7), ("999.1.1.1", 0)):
        completed, _ = run_snippet(tmp_path, RESOLVE, curl_output=output, curl_rc=rc)
        assert completed.returncode == 1, (output, rc, completed.stdout)
        assert "Could not detect your public IP" in completed.stderr
        assert "RANGES=" not in completed.stdout


def test_explicit_ranges_are_validated_and_kept(tmp_path):
    completed, curl_calls = run_snippet(
        tmp_path, RESOLVE, env={"CCFOUNDRY_SOURCE_RANGES": "198.51.100.0/24, 203.0.113.7/32"}
    )
    assert completed.returncode == 0, completed.stderr
    assert "RANGES=198.51.100.0/24,203.0.113.7/32" in completed.stdout
    assert "whole internet" not in completed.stderr
    assert curl_calls == []

    for bad in ("everyone", "203.0.113.7", "10.0.0.0/33", "300.1.1.1/32", "1.2.3.4/32;rm"):
        completed, _ = run_snippet(tmp_path, RESOLVE, env={"CCFOUNDRY_SOURCE_RANGES": bad})
        assert completed.returncode == 1, bad
        assert "must be comma-separated IPv4 CIDRs" in completed.stderr


def test_explicit_open_range_warns_loudly(tmp_path):
    completed, _ = run_snippet(tmp_path, RESOLVE, env={"CCFOUNDRY_SOURCE_RANGES": "0.0.0.0/0"})
    assert completed.returncode == 0, completed.stderr
    assert "RANGES=0.0.0.0/0" in completed.stdout
    assert "reachable from the whole internet" in completed.stderr
    assert "no login" in completed.stderr


def test_cloud_shell_without_terminal_requires_explicit_ranges(tmp_path):
    completed, curl_calls = run_snippet(tmp_path, RESOLVE, env={"CLOUD_SHELL": "true"}, curl_output="34.1.2.3")
    assert completed.returncode == 1
    assert "Cloud Shell cannot detect the IP of your browser" in completed.stderr
    # Cloud Shell's own egress address must never be used as the allowed range.
    assert curl_calls == []


def test_dry_run_uses_placeholder_instead_of_failing(tmp_path):
    completed, _ = run_snippet(tmp_path, "DRY_RUN=true\n" + RESOLVE, curl_rc=7)
    assert completed.returncode == 0, completed.stderr
    assert "RANGES=<your-public-ip>/32" in completed.stdout


def test_firewall_rule_uses_resolved_ranges(tmp_path):
    snippet = "DRY_RUN=true\nPROJECT_ID=demo-project\nSOURCE_RANGES=203.0.113.7/32\nensure_firewall_rule"
    completed, _ = run_snippet(tmp_path, snippet)
    assert completed.returncode == 0, completed.stderr
    assert "--source-ranges=203.0.113.7/32" in completed.stdout
    assert "0.0.0.0/0" not in completed.stdout
