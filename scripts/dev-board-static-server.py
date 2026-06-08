#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class DevBoardStaticHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        request_path = getattr(self, "path", "").split("?", 1)[0]
        if request_path in {"", "/", "/index.html"}:
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        elif request_path.startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Agent Dev Board static files with demo-safe cache headers.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    handler = lambda *handler_args, **handler_kwargs: DevBoardStaticHandler(
        *handler_args,
        directory=str(directory),
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving Agent Dev Board from {directory} on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
