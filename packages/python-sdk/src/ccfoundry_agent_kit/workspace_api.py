from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel


class WorkspaceWriteRequest(BaseModel):
    path: str
    content: str = ""


class WorkspaceRenameRequest(BaseModel):
    path: str
    new_path: str


class WorkspaceCopyRequest(BaseModel):
    path: str
    target_path: str


def build_workspace_router(workspace_root: str | Path) -> APIRouter:
    root = Path(workspace_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    def _safe_resolve(raw_path: str) -> Path:
        normalized = str(raw_path or "").strip().strip("/")
        target = (root / normalized).resolve()
        if target != root and root not in target.parents:
            raise HTTPException(status_code=400, detail="Path escapes workspace root")
        return target

    def _relative_path(path: Path) -> str:
        if path == root:
            return ""
        return path.relative_to(root).as_posix()

    def _serialize_tree(path: Path, *, depth: int) -> dict[str, Any]:
        stat = path.stat()
        payload: dict[str, Any] = {
            "name": path.name if path != root else "",
            "path": _relative_path(path),
            "type": "directory" if path.is_dir() else "file",
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
        if path.is_dir():
            children: list[dict[str, Any]] = []
            if depth > 0:
                for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                    children.append(_serialize_tree(child, depth=depth - 1))
            payload["children"] = children
        return payload

    @router.get("/tree")
    async def workspace_tree(path: str = "", depth: int = 3) -> dict[str, Any]:
        target = _safe_resolve(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Workspace path not found")
        if depth < 0:
            raise HTTPException(status_code=400, detail="depth must be >= 0")
        return {"root": _serialize_tree(target, depth=depth)}

    @router.get("/read")
    async def workspace_read(path: str) -> dict[str, Any]:
        target = _safe_resolve(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Workspace file not found")
        if target.is_dir():
            raise HTTPException(status_code=400, detail="Cannot read a directory")
        return {
            "path": _relative_path(target),
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }

    @router.put("/write")
    async def workspace_write(payload: WorkspaceWriteRequest) -> dict[str, Any]:
        target = _safe_resolve(payload.path)
        if target.exists() and target.is_dir():
            raise HTTPException(status_code=400, detail="Cannot overwrite a directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")
        return {
            "ok": True,
            "path": _relative_path(target),
            "bytes": len(payload.content.encode("utf-8")),
        }

    @router.post("/upload")
    async def workspace_upload(
        file: UploadFile = File(...),
        path: str = Form(""),
    ) -> dict[str, Any]:
        raw_path = str(path or "").strip()
        filename = file.filename or "upload.bin"
        base_target = _safe_resolve(raw_path or filename)
        treat_as_directory = (
            not raw_path
            or raw_path.endswith("/")
            or (base_target.exists() and base_target.is_dir())
            or not Path(raw_path).suffix
        )
        target = base_target / filename if treat_as_directory else base_target
        target.parent.mkdir(parents=True, exist_ok=True)
        data = await file.read()
        target.write_bytes(data)
        return {
            "ok": True,
            "path": _relative_path(target),
            "bytes": len(data),
            "filename": filename,
        }

    @router.delete("/delete")
    async def workspace_delete(path: str) -> dict[str, Any]:
        target = _safe_resolve(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Workspace path not found")
        if target == root:
            raise HTTPException(status_code=400, detail="Cannot delete workspace root")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True, "path": path}

    @router.post("/rename")
    async def workspace_rename(payload: WorkspaceRenameRequest) -> dict[str, Any]:
        source = _safe_resolve(payload.path)
        target = _safe_resolve(payload.new_path)
        if not source.exists():
            raise HTTPException(status_code=404, detail="Workspace path not found")
        if source == root:
            raise HTTPException(status_code=400, detail="Cannot rename workspace root")
        if target.exists():
            raise HTTPException(status_code=409, detail="Target path already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return {
            "ok": True,
            "path": _relative_path(source),
            "new_path": _relative_path(target),
        }

    @router.post("/copy")
    async def workspace_copy(payload: WorkspaceCopyRequest) -> dict[str, Any]:
        source = _safe_resolve(payload.path)
        target = _safe_resolve(payload.target_path)
        if not source.exists():
            raise HTTPException(status_code=404, detail="Workspace path not found")
        if target.exists():
            raise HTTPException(status_code=409, detail="Target path already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        return {
            "ok": True,
            "path": _relative_path(source),
            "target_path": _relative_path(target),
        }

    @router.post("/move")
    async def workspace_move(payload: WorkspaceCopyRequest) -> dict[str, Any]:
        source = _safe_resolve(payload.path)
        target = _safe_resolve(payload.target_path)
        if not source.exists():
            raise HTTPException(status_code=404, detail="Workspace path not found")
        if source == root:
            raise HTTPException(status_code=400, detail="Cannot move workspace root")
        if target.exists():
            raise HTTPException(status_code=409, detail="Target path already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {
            "ok": True,
            "path": _relative_path(source),
            "target_path": _relative_path(target),
        }

    return router
