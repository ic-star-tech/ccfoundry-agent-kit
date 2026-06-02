from __future__ import annotations

import os
from pathlib import Path

from agent_dev_board_api.cloud_run import CloudRunManager


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_status_rejects_expired_gcloud_token(monkeypatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "gcloud",
        """#!/usr/bin/env sh
if [ "$1" = "--version" ]; then
  echo "Google Cloud SDK test"
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "print-access-token" ]; then
  echo "ERROR: (gcloud.auth.print-access-token) Reauthentication failed." >&2
  exit 1
fi
exit 1
""",
    )
    _write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env sh
if [ "$1" = "--version" ]; then
  echo "Docker version test"
  exit 0
fi
exit 1
""",
    )

    gcloud_config = tmp_path / "gcloud"
    (gcloud_config / "configurations").mkdir(parents=True)
    (gcloud_config / "active_config").write_text("default\n", encoding="utf-8")
    (gcloud_config / "configurations" / "config_default").write_text(
        "[core]\naccount = dev@example.com\nproject = test-project\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud_config))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    status = CloudRunManager(repo_root=tmp_path, runtime_dir=tmp_path / "runtime").status()

    assert status["ok"] is False
    assert status["gcloud"]["active_account"] == "dev@example.com"
    assert status["gcloud"]["authenticated"] is False
    assert status["gcloud"]["token_valid"] is False
    assert "Reauthentication failed" in status["gcloud"]["auth_error"]
    assert any("needs refresh" in error for error in status["errors"])
