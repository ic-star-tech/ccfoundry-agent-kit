from __future__ import annotations

import asyncio
import inspect
import json
import os
import pty
import fcntl
import struct
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .agent_space import AgentSpace
from .bootstrap import FoundryApprovalPayload, FoundryBootstrap, FoundryDeveloperClaimPayload, FoundryInvitePayload
from .models import AgentManifest, ChatRequest, ChatResponse, ContextMode, HealthResponse
from .pull_runtime import FoundryPullRuntime
from .workspace_api import build_workspace_router

ChatHandler = Callable[[ChatRequest, AgentSpace], ChatResponse | Awaitable[ChatResponse]]


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_agent_app(
    *,
    manifest: AgentManifest,
    chat_handler: ChatHandler,
    agent_space_dir: str | Path,
    foundry_bootstrap: FoundryBootstrap | None = None,
) -> FastAPI:
    app = FastAPI(title=manifest.label, version=manifest.version)
    agent_space = AgentSpace(Path(agent_space_dir))
    bootstrap_state = foundry_bootstrap.state if foundry_bootstrap else None

    app.include_router(build_workspace_router(agent_space.workspace_dir))

    def _native_terminal_enabled() -> bool:
        if not foundry_bootstrap:
            return False
        provider = str(foundry_bootstrap.config.terminal_provider or "none").strip().lower()
        features = {
            str(item).strip().lower()
            for item in (foundry_bootstrap.config.workspace_features or [])
            if str(item).strip()
        }
        return provider == "agent_native" and "terminal" in features

    def _resize_pty(master_fd: int, cols: int, rows: int) -> None:
        payload = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        fcntl.ioctl(master_fd, 0x5414, payload)

    async def _run_chat(request: ChatRequest) -> ChatResponse:
        result = chat_handler(request, agent_space)
        if inspect.isawaitable(result):
            result = await result
        if result.notes_update.strip():
            agent_space.append_note(result.notes_update, source=request.mode.value)
        return result

    def _coerce_mode(raw_value: Any, *, inline_hint: bool = False) -> ContextMode:
        normalized = str(raw_value or "").strip().lower()
        if normalized == ContextMode.INLINE.value or inline_hint:
            return ContextMode.INLINE
        return ContextMode.DIRECT

    def _normalize_foundry_chat_payload(payload: dict[str, Any]) -> ChatRequest:
        metadata = payload.get("metadata")
        merged_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if payload.get("user_id"):
            merged_metadata.setdefault("foundry_user_id", str(payload.get("user_id")))
        if payload.get("session_id"):
            merged_metadata.setdefault("foundry_session_id", str(payload.get("session_id")))
        if payload.get("skill_ref"):
            merged_metadata.setdefault("skill_ref", str(payload.get("skill_ref")))
        images = list(payload.get("images") or [])
        files = list(payload.get("files") or [])
        if images:
            merged_metadata.setdefault("images", images)
        if files:
            merged_metadata.setdefault("files", files)
        inline_context = payload.get("inline_context")
        inline_hint = isinstance(inline_context, dict) and inline_context.get("mode") == "inline"
        history = payload.get("history")
        return ChatRequest(
            message=str(payload.get("message") or payload.get("content") or ""),
            mode=_coerce_mode(payload.get("mode"), inline_hint=inline_hint),
            username=str(payload.get("username") or payload.get("user_id") or "foundry-user"),
            user_id=str(payload.get("user_id") or ""),
            context=str(payload.get("context") or ""),
            conversation_id=str(payload.get("conversation_id") or payload.get("session_id") or ""),
            session_id=str(payload.get("session_id") or payload.get("conversation_id") or ""),
            history=list(history) if isinstance(history, list) else [],
            images=images,
            files=files,
            skill_ref=str(payload.get("skill_ref") or ""),
            inline_context=inline_context if isinstance(inline_context, dict) else {},
            stream=bool(payload.get("stream")),
            metadata=merged_metadata,
        )

    pull_runtime = (
        FoundryPullRuntime(
            bootstrap=foundry_bootstrap,
            normalize_payload=_normalize_foundry_chat_payload,
            run_chat=_run_chat,
        )
        if foundry_bootstrap
        else None
    )

    @app.get("/health")
    async def health() -> HealthResponse:
        return HealthResponse(agent=manifest.name, version=manifest.version)

    @app.get("/manifest")
    async def get_manifest() -> AgentManifest:
        return manifest

    @app.get("/.well-known/agent-card.json")
    async def get_agent_card() -> dict:
        if foundry_bootstrap:
            return foundry_bootstrap.build_agent_card()
        return {
            "name": manifest.name,
            "description": manifest.description,
            "version": manifest.version,
            "url": "",
            "preferredTransport": "REST",
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": capability,
                    "name": capability.replace("_", " ").title(),
                    "description": f"{manifest.label} supports {capability}",
                    "tags": [capability],
                }
                for capability in manifest.capabilities
            ],
        }

    @app.post("/chat")
    async def chat(request: ChatRequest) -> ChatResponse:
        return await _run_chat(request)

    @app.post("/api/chat")
    async def foundry_chat(payload: dict[str, Any]) -> StreamingResponse:
        request = _normalize_foundry_chat_payload(payload if isinstance(payload, dict) else {})

        async def event_generator():
            try:
                yield _sse_event("step", {"status": "running", "text": "Processing request..."})
                result = await _run_chat(request)
                yield _sse_event("step", {"status": "done", "text": "Request completed."})
                chunk_size = 80
                for index in range(0, len(result.reply), chunk_size):
                    chunk = result.reply[index : index + chunk_size]
                    if chunk:
                        yield _sse_event("token", {"content": chunk})
                yield _sse_event("message", result.message_payload())
            except Exception as exc:
                yield _sse_event("error", {"detail": str(exc)})
            finally:
                yield _sse_event("done", {})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/reflections")
    async def get_reflections(date: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return agent_space.list_reflections(date=date, limit=limit)

    @app.on_event("startup")
    async def startup_pull_runtime() -> None:
        if pull_runtime:
            await pull_runtime.start()

    @app.on_event("shutdown")
    async def shutdown_pull_runtime() -> None:
        if pull_runtime:
            await pull_runtime.stop()

    if _native_terminal_enabled():
        @app.websocket("/pty")
        async def pty_websocket(websocket: WebSocket) -> None:
            expected_secret = str((bootstrap_state.env_vars if bootstrap_state else {}).get("AGENT_SECRET") or "").strip()
            auth_header = str(websocket.headers.get("authorization") or "").strip()
            token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else auth_header
            if expected_secret and token != expected_secret:
                await websocket.close(code=1008)
                return

            await websocket.accept()

            shell = (
                os.getenv("SHELL", "").strip()
                or "/bin/bash"
                if Path("/bin/bash").exists()
                else "/bin/sh"
            )
            workspace_dir = Path(agent_space_dir) / "workspace"
            workspace_dir.mkdir(parents=True, exist_ok=True)

            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                [shell, "-i"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=workspace_dir,
                start_new_session=True,
                text=False,
                env=os.environ.copy(),
            )
            os.close(slave_fd)

            queue: asyncio.Queue[str] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            stop_event = asyncio.Event()

            def reader() -> None:
                try:
                    while not stop_event.is_set():
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, data.decode(errors="ignore"))
                except OSError:
                    pass
                finally:
                    loop.call_soon_threadsafe(stop_event.set)

            reader_task = asyncio.create_task(asyncio.to_thread(reader))

            async def forward_output() -> None:
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(queue.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        if proc.poll() is not None:
                            stop_event.set()
                        continue
                    await websocket.send_text(chunk)

            async def forward_input() -> None:
                while True:
                    message = await websocket.receive_text()
                    payload = json.loads(message)
                    msg_type = payload.get("type")
                    if msg_type == "input":
                        os.write(master_fd, str(payload.get("data", "")).encode())
                    elif msg_type == "resize":
                        _resize_pty(master_fd, int(payload.get("cols", 80)), int(payload.get("rows", 24)))

            output_task = asyncio.create_task(forward_output())
            input_task = asyncio.create_task(forward_input())

            try:
                await asyncio.wait(
                    [output_task, input_task, reader_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except WebSocketDisconnect:
                pass
            finally:
                stop_event.set()
                for task in (output_task, input_task, reader_task):
                    task.cancel()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
                    except asyncio.TimeoutError:
                        proc.kill()
                os.close(master_fd)
                try:
                    await websocket.close()
                except Exception:
                    pass

    @app.get("/foundry/bootstrap/state")
    async def get_foundry_bootstrap_state() -> dict:
        if not foundry_bootstrap:
            return {"enabled": False}
        return foundry_bootstrap.public_state()

    @app.post("/foundry/bootstrap/invite")
    async def receive_foundry_invite(
        payload: FoundryInvitePayload,
        x_foundry_bootstrap_token: str | None = Header(default=None),
    ) -> dict:
        if not foundry_bootstrap:
            raise HTTPException(status_code=503, detail="Foundry bootstrap not enabled")
        try:
            foundry_bootstrap.verify_callback_token(x_foundry_bootstrap_token)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            return await foundry_bootstrap.handle_invite(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/foundry/bootstrap/approved")
    async def receive_foundry_approval(
        payload: FoundryApprovalPayload,
        x_foundry_bootstrap_token: str | None = Header(default=None),
    ) -> dict:
        if not foundry_bootstrap:
            raise HTTPException(status_code=503, detail="Foundry bootstrap not enabled")
        try:
            foundry_bootstrap.verify_callback_token(x_foundry_bootstrap_token)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            return await foundry_bootstrap.handle_approval(payload)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/foundry/bootstrap/developer-claim")
    async def install_developer_claim(
        payload: FoundryDeveloperClaimPayload,
        request: Request,
    ) -> dict:
        if not foundry_bootstrap:
            raise HTTPException(status_code=503, detail="Foundry bootstrap not enabled")
        client_host = str(getattr(getattr(request, "client", None), "host", "") or "").strip()
        if client_host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="Developer claim updates are restricted to local callers")
        try:
            return await foundry_bootstrap.install_developer_claim(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if foundry_bootstrap:
        @app.on_event("startup")
        async def startup_foundry_bootstrap() -> None:
            await foundry_bootstrap.start()

        @app.on_event("shutdown")
        async def shutdown_foundry_bootstrap() -> None:
            await foundry_bootstrap.stop()

    return app
