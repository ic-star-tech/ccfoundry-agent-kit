"""Workspace API security regression tests.

Covers symlink escape and path traversal vectors for upload and write endpoints.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from ccfoundry_agent_kit.workspace_api import build_workspace_router


@pytest.fixture()
def workspace_app(tmp_path: Path):
    """Create a minimal FastAPI app with the workspace router."""
    app = FastAPI()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    app.include_router(build_workspace_router(workspace_root))
    return app, workspace_root


@pytest.fixture()
def client(workspace_app):
    app, _ = workspace_app
    return TestClient(app)


class TestUploadSymlinkEscape:
    """Symlink in workspace pointing outside should not be writable via upload."""

    def test_upload_through_symlink_is_rejected(self, workspace_app, tmp_path: Path):
        app, workspace_root = workspace_app
        client = TestClient(app)

        # Create an external file and a symlink inside workspace pointing to it
        external_file = tmp_path / "outside.txt"
        external_file.write_text("original content", encoding="utf-8")

        sub_dir = workspace_root / "sub"
        sub_dir.mkdir()
        symlink_path = sub_dir / "link.txt"
        symlink_path.symlink_to(external_file)

        # Attempt to upload through the symlink
        response = client.post(
            "/api/workspace/upload",
            files={"file": ("link.txt", b"PWNED", "text/plain")},
            data={"path": "sub/"},
        )

        # Should be rejected
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"

        # External file should NOT have been modified
        assert external_file.read_text(encoding="utf-8") == "original content"

    def test_upload_through_symlink_absolute_target(self, workspace_app, tmp_path: Path):
        """Symlink pointing to /tmp should also be caught."""
        app, workspace_root = workspace_app
        client = TestClient(app)

        external_file = tmp_path / "secret.txt"
        external_file.write_text("secret", encoding="utf-8")

        symlink_path = workspace_root / "escape.txt"
        symlink_path.symlink_to(external_file)

        response = client.post(
            "/api/workspace/upload",
            files={"file": ("escape.txt", b"overwritten", "text/plain")},
            data={"path": "escape.txt"},
        )

        assert response.status_code == 400
        assert external_file.read_text(encoding="utf-8") == "secret"


class TestUploadPathTraversal:
    """Malicious filenames should not escape workspace."""

    def test_traversal_filename_is_sanitized(self, workspace_app, tmp_path: Path):
        app, workspace_root = workspace_app
        client = TestClient(app)

        response = client.post(
            "/api/workspace/upload",
            files={"file": ("../../etc/passwd", b"fake", "text/plain")},
            data={"path": ""},
        )

        # Should succeed but write to workspace/passwd (basename sanitized)
        assert response.status_code == 200
        # File should be inside workspace, not at ../../etc/passwd
        assert not (tmp_path / "etc" / "passwd").exists()

    def test_dotdot_filename_rejected(self, workspace_app):
        app, _ = workspace_app
        client = TestClient(app)

        response = client.post(
            "/api/workspace/upload",
            files={"file": ("..", b"data", "text/plain")},
            data={"path": ""},
        )
        assert response.status_code == 400


class TestWriteSymlinkEscape:
    """PUT /write should also reject symlink targets."""

    def test_write_through_symlink_is_rejected(self, workspace_app, tmp_path: Path):
        app, workspace_root = workspace_app
        client = TestClient(app)

        external_file = tmp_path / "outside_write.txt"
        external_file.write_text("original", encoding="utf-8")

        symlink_path = workspace_root / "link_write.txt"
        symlink_path.symlink_to(external_file)

        response = client.put(
            "/api/workspace/write",
            json={"path": "link_write.txt", "content": "PWNED"},
        )

        assert response.status_code == 400
        assert external_file.read_text(encoding="utf-8") == "original"


class TestNormalUpload:
    """Normal uploads should still work."""

    def test_simple_upload(self, workspace_app):
        app, workspace_root = workspace_app
        client = TestClient(app)

        response = client.post(
            "/api/workspace/upload",
            files={"file": ("test.txt", b"hello world", "text/plain")},
            data={"path": "test.txt"},
        )

        assert response.status_code == 200
        uploaded = workspace_root / "test.txt"
        assert uploaded.exists()
        assert uploaded.read_bytes() == b"hello world"

    def test_upload_to_subdirectory(self, workspace_app):
        app, workspace_root = workspace_app
        client = TestClient(app)

        response = client.post(
            "/api/workspace/upload",
            files={"file": ("data.bin", b"\x00\x01\x02", "application/octet-stream")},
            data={"path": "subdir/"},
        )

        assert response.status_code == 200
        uploaded = workspace_root / "subdir" / "data.bin"
        assert uploaded.exists()
        assert uploaded.read_bytes() == b"\x00\x01\x02"
