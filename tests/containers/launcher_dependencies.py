"""Real packed-CLI fixture. Prepare with network; exercise with Docker --network=none.

Requires Python 3.10+, Node/npm and two throwaway TLS certificates in certs/.
All registries, processes and writes belong to the supplied disposable root.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request


def run(args, *, cwd, env=None, log=None, success=True):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=420)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout)
    if success and result.returncode:
        raise AssertionError(f"Command failed ({result.returncode}); see {log}: {args[0]}")
    if not success and result.returncode == 0:
        raise AssertionError(f"Expected failure; see {log}")
    return result.stdout


def prepare(root):
    source = root / "source"
    assets = root / "assets"
    assets.mkdir()
    wheels = assets / "wheel house"
    wheels.mkdir()
    packages = [source / name for name in ("packages/python-sdk", "examples/me_agent", "apps/agent-dev-board-api")]
    run(["python", "-m", "pip", "download", "--only-binary=:all:", "--dest", str(wheels),
         "pip", "setuptools", "wheel", *map(str, packages)], cwd=root, log=root / "out/prepare-pip.log")
    run(["npm", "ci", "--cache", str(assets / "npm cache"), "--no-audit", "--no-fund"],
        cwd=source / "apps/agent-dev-board-web", log=root / "out/prepare-npm.log")
    packed = json.loads(run(["npm", "pack", "--json", "--pack-destination", str(assets)], cwd=source,
                            log=root / "out/pack.log"))[0]
    (assets / "package-name").write_text(packed["filename"])
    assert "scripts/dev-board-deps.mjs" in {f["path"] for f in packed["files"]}
    mirror = assets / "mirror"
    for wheel in wheels.glob("*.whl"):
        name = wheel.name.split("-", 1)[0].lower().replace("_", "-").replace(".", "-")
        directory = mirror / "simple" / name
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wheel, directory / wheel.name)
    for directory in (mirror / "simple").iterdir():
        (directory / "index.html").write_text("\n".join(
            f'<a href="{p.name}#sha256={hashlib.sha256(p.read_bytes()).hexdigest()}">{p.name}</a>'
            for p in directory.glob("*.whl")))
    lock = json.loads((source / "apps/agent-dev-board-web/package-lock.json").read_text())
    def supported(item):
        return (not item.get("os") or "linux" in item["os"]) and (not item.get("cpu") or "x64" in item["cpu"]) and (not item.get("libc") or "glibc" in item["libc"])
    urls = {v["resolved"] for v in lock["packages"].values() if v.get("resolved", "").startswith("https://registry.npmjs.org/") and supported(v)}
    def fetch(url):
        path = mirror / "npm" / urllib.parse.urlsplit(url).path.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as response:
            path.write_bytes(response.read())
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, sorted(urls)))
    (root / "out/preparation.json").write_text(json.dumps({
        "wheels": len(list(wheels.glob("*.whl"))), "npm_tarballs": len(urls),
        "package": packed["filename"], "sha256": hashlib.sha256((assets / packed["filename"]).read_bytes()).hexdigest(),
        "python": run(["python", "--version"], cwd=root).strip(),
        "node": run(["node", "--version"], cwd=root).strip(),
        "npm": run(["npm", "--version"], cwd=root).strip(),
    }, indent=2))


def exercise(root):
    assets = root / "assets"
    results = []
    def passed(name):
        results.append(name)
        print(f"PASS {name}", flush=True)
        (root / "out/result.json").write_text(json.dumps({"checks": results, "network": "Docker --network=none"}, indent=2))
    base_env = dict(os.environ, PIP_CONFIG_FILE=os.devnull, PIP_RETRIES="0", PIP_TIMEOUT="3",
                    npm_config_fetch_retries="0", npm_config_fetch_timeout="5000", npm_config_update_notifier="false")
    for key in list(base_env):
        if key.upper().endswith("_PROXY"):
            base_env.pop(key)
    def unpack(name):
        case = root / "cases" / name
        case.mkdir(parents=True)
        run(["npm", "install", "--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(case / "cli"),
             str(assets / (assets / "package-name").read_text())], cwd=case, env=base_env, log=root / f"out/{name}-unpack.log")
        kit = case / "cli/node_modules/ccfoundry"
        command = ["node", str(kit / "scripts/dev-board.mjs"), "--workspace", str(case / "fresh workspace")]
        return case, kit, command
    case, kit, command = unpack("offline")
    offline = ["--offline", "--wheelhouse", str(assets / "wheel house"), "--npm-cache", str(assets / "npm cache")]
    python_stamp = case / "fresh workspace/.venv/.ccfoundry-dev-board.stamp"
    web_stamp = kit / "apps/agent-dev-board-web/node_modules/.ccfoundry-dev-board.stamp"
    def setup(name, *, extra=offline, env=base_env, success=True, cmd=command):
        return run([*cmd, *extra, "--setup-only"], cwd=root, env=env, success=success, log=root / f"out/{name}.log")
    setup("offline-cold")
    assert python_stamp.exists() and web_stamp.exists()
    assert not (case / "fresh workspace/.dev-board").exists()
    passed("packed CLI bootstraps a new workspace from wheels/cache with network disabled")
    output = setup("offline-reuse")
    assert "Installing Python" not in output and "Installing web" not in output
    assert "Python dependencies are ready" in output and "Web dependencies are ready" in output
    passed("repeat setup checks and reuses both dependency environments")
    project = kit / "examples/me_agent/pyproject.toml"
    old_stat = project.stat()
    project.write_text(project.read_text() + "\n# dependency fixture: same timestamp\n")
    os.utime(project, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    output = setup("offline-content-change")
    assert "Installing Python" in output and "Installing web" not in output
    passed("metadata content change with preserved timestamps refreshes Python only")
    constraint = case / "impossible.txt"
    constraint.write_text("fastapi==0.0.0\n")
    output = setup("offline-missing-wheel", env=dict(base_env, PIP_CONSTRAINT=str(constraint)), success=False)
    assert not python_stamp.exists() and "dependencies are not marked ready" in output
    output = setup("offline-recover-python")
    assert "Installing Python" in output
    passed("failed wheel resolution removes the success marker; restored inputs reinstall")
    empty_cache = case / "empty npm cache"
    output = setup("offline-missing-cache", extra=[*offline[:-1], str(empty_cache)], success=False)
    assert not web_stamp.exists() and "Installing Python" not in output
    assert "ENOTCACHED" in output
    output = setup("offline-recover-web")
    assert "Installing web" in output and "Installing Python" not in output
    passed("empty npm cache fails without network fallback; restored cache recovers")
    has_projects = (kit / "apps/agent-dev-board-api/src/agent_dev_board_api/projects.py").exists()
    service_command = list(command)
    if has_projects:
        project = case / "customer source"
        (project / "src").mkdir(parents=True)
        (project / "agent_space").mkdir()
        (project / "agent_space/config.yaml").write_text("name: offline_customer\n")
        (project / "src/customer.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
            '@app.get("/health")\ndef health(): return {"status":"healthy"}\n'
            '@app.post("/chat")\ndef chat(body: dict): return {"reply":"offline-customer:" + body["message"]}\n')
        (project / "ccfoundry.project.json").write_text(json.dumps({
            "version": 1, "app_module": "customer:app",
            "python": str(case / "fresh workspace/.venv/bin/python"),
        }))
        service_command += ["--project", str(project)]
    def api(path, payload=None):
        request = urllib.request.Request("http://127.0.0.1:8090" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    server_log = root / "out/offline-services.log"
    with server_log.open("w") as handle:
        child = subprocess.Popen([*service_command, *offline], cwd=root, env=base_env, stdout=handle,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for port, path in [(8090, "/health"), (3000, "/")]:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    assert child.poll() is None, f"Launcher exited; see {server_log}"
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(.2)
                else:
                    raise AssertionError(f"Service did not become ready: {port}")
            if has_projects:
                template = next(t for t in api("/api/local-agent-templates") if t["kind"] == "project")
                item = api("/api/local-agents", {"template_id": template["id"], "name": "offline_customer", "auto_start": False})
                try:
                    api("/api/local-agents/offline_customer/start", {})
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            request = urllib.request.Request(item["base_url"] + "/chat",
                                data=b'{"message":"hello"}', headers={"Content-Type":"application/json"})
                            with urllib.request.urlopen(request, timeout=2) as response:
                                assert json.load(response)["reply"] == "offline-customer:hello"
                            break
                        except OSError:
                            if time.monotonic() > deadline:
                                raise
                            time.sleep(.2)
                    passed("combined packed CLI registers and runs customer source without GitHub/network access")
                finally:
                    api("/api/local-agents/offline_customer/stop", {})
        finally:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
    passed("actual API and Vite serve HTTP after offline packaged setup")

    requests = []
    authenticated = []
    class Registry(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path.startswith("/npm/"):
                authenticated.append(self.headers.get("Authorization") in {"Bearer fixture-token-one", "Bearer fixture-token-two"})
            return super().do_GET()
        def log_message(self, *_):
            pass
    handler = functools.partial(Registry, directory=str(assets / "mirror"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "certs/server.pem", root / "certs/server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        mirror_case, mirror_kit, mirror_command = unpack("mirror")
        authority = f"127.0.0.1:{server.server_port}"
        pip_config = mirror_case / "pip.conf"
        pip_config.write_text(f"[global]\nindex-url = https://{authority}/simple/\ncert = {root}/certs/ca.pem\n")
        npm_config = mirror_case / "npmrc"
        ca = mirror_case / "npm-ca.pem"
        shutil.copyfile(root / "certs/ca.pem", ca)
        npm_config.write_text(f"registry=https://{authority}/npm/\ncafile={ca}\nstrict-ssl=true\n//{authority}/npm/:_authToken=${{LAUNCHER_FIXTURE_TOKEN}}\n")
        mirror_env = dict(base_env, PIP_CONFIG_FILE=str(pip_config), PIP_NO_CACHE_DIR="1",
                          npm_config_userconfig=str(npm_config), LAUNCHER_FIXTURE_TOKEN="fixture-token-one")
        mirror_options = ["--npm-cache", str(mirror_case / "npm cache")]
        setup("mirror-cold", cmd=mirror_command, env=mirror_env, extra=mirror_options)
        assert any(p.startswith("/simple/") and p.endswith(".whl") for p in requests)
        assert any(p.startswith("/npm/") and p.endswith(".tgz") for p in requests)
        mirror_python_stamp = mirror_case / "fresh workspace/.venv/.ccfoundry-dev-board.stamp"
        mirror_web_stamp = mirror_kit / "apps/agent-dev-board-web/node_modules/.ccfoundry-dev-board.stamp"
        for stamp in [mirror_python_stamp, mirror_web_stamp]:
            assert "fixture-token" not in stamp.read_text() and "https://" not in stamp.read_text()
        passed("native pip/npm private HTTPS configuration and private CA install real dependencies")
        output = setup("mirror-reuse", cmd=mirror_command, env=mirror_env, extra=mirror_options)
        assert "Installing Python" not in output and "Installing web" not in output
        passed("effective mirror configuration produces reusable opaque success markers")
        mirror_env["LAUNCHER_FIXTURE_TOKEN"] = "fixture-token-two"
        output = setup("mirror-token-change", cmd=mirror_command, env=mirror_env, extra=mirror_options)
        assert "Installing web" in output and "Installing Python" not in output
        assert authenticated and all(authenticated)
        passed("native npmrc credential environment changes refresh only web dependencies")
        old_stat = ca.stat()
        shutil.copyfile(root / "certs/wrong-ca.pem", ca)
        os.utime(ca, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        # Empty cache ensures TLS is exercised instead of successful local tarball reuse.
        shutil.rmtree(mirror_case / "npm cache")
        output = setup("mirror-wrong-npm-ca", cmd=mirror_command, env=mirror_env, extra=mirror_options, success=False)
        assert not mirror_web_stamp.exists() and "Installing Python" not in output
        # Some npm 10 releases print only an internal exit-handler error on
        # TLS failure. Require the actual TLS rejection from its diagnostic log.
        diagnostic = "\n".join(p.read_text() for p in (mirror_case / "npm cache/_logs").glob("*.log"))
        (root / "out/mirror-wrong-npm-ca-debug.log").write_text(diagnostic)
        assert any(code in output + diagnostic for code in (
            "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY"))
        shutil.copyfile(root / "certs/ca.pem", ca)
        setup("mirror-recover-npm-ca", cmd=mirror_command, env=mirror_env, extra=mirror_options)
        passed("npm CA content changes invalidate the stamp; untrusted CA fails without TLS bypass")
        pip_config.write_text(pip_config.read_text().replace("/certs/ca.pem", "/certs/wrong-ca.pem"))
        output = setup("mirror-wrong-pip-ca", cmd=mirror_command, env=mirror_env, extra=mirror_options, success=False)
        assert not mirror_python_stamp.exists()
        assert "CERTIFICATE_VERIFY_FAILED" in output or "certificate verify failed" in output
        pip_config.write_text(pip_config.read_text().replace("/certs/wrong-ca.pem", "/certs/ca.pem"))
        setup("mirror-recover-pip-ca", cmd=mirror_command, env=mirror_env, extra=mirror_options)
        passed("pip rejects an untrusted CA; restoring the private CA recovers installation")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (root / "out/mirror-requests.json").write_text(json.dumps(requests, indent=2))
    assert len(results) == 11 + int(has_projects)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "exercise"])
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    {"prepare": prepare, "exercise": exercise}[arguments.mode](arguments.root.resolve())
