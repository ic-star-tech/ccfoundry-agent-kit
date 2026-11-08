"""Synthetic MCP/Foundry/model peers. Only loopback or a child process."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid


TOOL = {
    "name": "search",
    "description": "Search synthetic fixtures",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


class Peer:
    def __init__(self):
        self.calls = []
        self.requests = []
        self.model_requests = []
        self.status = None
        self.error_method = "initialize"
        self.delay_method = None
        self.delay = 0.3
        self.malformed = False
        self.rpc_error = False
        self.tool_error = False
        self.has_tools = True
        self.stream_reply = False
        self.redirect = None
        self.result = "fixture search answer"
        self.tools = [TOOL]
        self.pagination = False
        self.repeat_cursor = False
        self.model_mode = "answer"
        self.grants = {}
        self.retired = False
        self.after_call = None
        self.stop = threading.Event()
        self.events = {}

    def rpc(self, message):
        method = message["method"]
        self.calls.append(method)
        if method == self.delay_method:
            time.sleep(self.delay)
        if "id" not in message:
            return None
        response = {"jsonrpc": "2.0", "id": message["id"]}
        if method == self.error_method and self.rpc_error:
            response["error"] = {
                "code": -32603,
                "message": "fixture-private-upstream-error",
            }
        elif method == "initialize":
            response["result"] = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}} if self.has_tools else {},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            if self.pagination and not message.get("params", {}).get("cursor"):
                response["result"] = {"tools": [], "nextCursor": "page-two"}
            else:
                response["result"] = {"tools": self.tools}
                if self.repeat_cursor:
                    response["result"]["nextCursor"] = "page-two"
        elif method == "tools/call":
            assert message["params"]["name"] == "search"
            response["result"] = {
                "isError": self.tool_error,
                "content": [{"type": "text", "text": self.result}],
            }
            if self.after_call:
                self.after_call()
        else:
            response["error"] = {"code": -32601, "message": "unknown method"}
        return response

    def model(self, body):
        self.model_requests.append(body)
        prior = [m for m in body["messages"] if m["role"] == "tool"]
        if prior and self.model_mode == "answer":
            assert "fixture search answer" in prior[-1]["content"]
            message = {
                "role": "assistant",
                "content": "The synthetic source confirms the answer.",
            }
        else:
            tool = body["tools"][0]["function"]["name"]
            if self.model_mode == "unknown":
                tool = "unoffered_tool"
            call_id = (
                "same"
                if self.model_mode == "duplicate"
                else "call-" + str(len(self.model_requests))
            )
            args = "{bad" if self.model_mode == "invalid" else '{"query":"synthetic"}'
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tool, "arguments": args},
                    }
                ],
            }
        return {
            "id": "fixture-completion",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls"
                    if "tool_calls" in message
                    else "stop",
                }
            ],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, body=None, headers=None):
        encoded = (
            b""
            if body is None
            else body
            if isinstance(body, bytes)
            else json.dumps(body).encode()
        )
        self.send_response(code)
        self.send_header(
            "Content-Type", (headers or {}).get("Content-Type", "application/json")
        )
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (headers or {}).items():
            if key != "Content-Type":
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path != "/sse":
            self.send(405)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        session_id = uuid.uuid4().hex
        events = queue.Queue()
        self.server.peer.events[session_id] = events
        self.wfile.write(
            (
                "event: endpoint\ndata: /messages?sessionId=" + session_id + "\n\n"
            ).encode()
        )
        self.wfile.flush()
        while not self.server.peer.stop.is_set():
            try:
                payload = events.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.wfile.write(
                    b"event: message\ndata: " + json.dumps(payload).encode() + b"\n\n"
                )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break

    def do_DELETE(self):
        self.send(204)

    def do_POST(self):
        peer = self.server.peer
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        peer.requests.append((self.path, dict(self.headers), body))
        if self.path == "/api/agents/fixture-agent/registry/sync":
            assert self.headers["Authorization"] == "Bearer fixture-agent-secret"
            assert set(body) == {"capabilities"} and set(body["capabilities"]) == {
                "mcp_services"
            }
            self.send(
                200,
                {
                    "ok": True,
                    "retired": peer.retired,
                    "allocated_resources": {}
                    if peer.retired
                    else {"mcp_servers": peer.grants},
                },
            )
            return
        if self.path == "/v1/chat/completions":
            if peer.model_mode == "model-error" and peer.model_requests:
                peer.model_requests.append(body)
                self.send(500, {"error": {"message": "fixture-private-model-secret"}})
                return
            self.send(200, peer.model(body))
            return
        method = body["method"]
        if self.path == "/redirect" and peer.redirect:
            self.send(307, headers={"Location": peer.redirect})
            return
        if method == peer.error_method and peer.status:
            self.send(peer.status, {"detail": "fixture-private-upstream-error"})
            return
        if peer.malformed and method == peer.error_method:
            self.send(200, b"not a JSON-RPC response")
            return
        result = peer.rpc(body)
        if self.path.startswith("/messages"):
            if result:
                session_id = parse_qs(urlsplit(self.path).query)["sessionId"][0]
                peer.events[session_id].put(result)
            self.send(202)
        elif result:
            headers = (
                {"Mcp-Session-Id": "fixture-session"} if method == "initialize" else {}
            )
            if peer.stream_reply:
                headers["Content-Type"] = "text/event-stream"
                self.send(
                    200,
                    b"event: message\ndata: " + json.dumps(result).encode() + b"\n\n",
                    headers,
                )
            else:
                self.send(200, result, headers)
        else:
            self.send(202)


@contextmanager
def http_peer():
    peer = Peer()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.peer = peer
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    peer.url = "http://127.0.0.1:" + str(server.server_port)
    try:
        yield peer
    finally:
        peer.stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def stdio():
    peer = Peer()
    if os.environ.get("FIXTURE_PID_FILE"):
        Path(os.environ["FIXTURE_PID_FILE"]).write_text(str(os.getpid()))
    peer.result = json.dumps(
        {
            "secret_inherited": "FOUNDRY_PARENT_SECRET" in os.environ,
            "explicit": os.environ.get("FIXTURE_EXPLICIT"),
        }
    )
    if os.environ.get("FIXTURE_HANG_CALL"):
        peer.delay_method, peer.delay = "tools/call", 60
    for line in sys.stdin:
        response = peer.rpc(json.loads(line))
        if response:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    if "--modern" in sys.argv:
        from mcp.server import MCPServer

        server = MCPServer("modern-fixture", log_level="ERROR")

        @server.tool()
        def search(query: str) -> str:
            return "modern fixture: " + query

        server.run(transport="stdio")
    else:
        stdio()
