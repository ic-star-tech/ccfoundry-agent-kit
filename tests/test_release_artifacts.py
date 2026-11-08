"""Consumer checks and deliberately corrupted real distribution archives."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_release", ROOT / "scripts/prepare_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture(scope="session")
def candidate():
    directory = os.environ.get("CCFOUNDRY_RELEASE_ARTIFACTS")
    if not directory:
        pytest.fail("Build a candidate and set CCFOUNDRY_RELEASE_ARTIFACTS; no skip fallback")
    path = Path(directory).resolve()
    manifest = json.loads((path / "release-manifest.json").read_text())
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert manifest["source_commit"] == head
    return path, manifest


def test_real_archives_agree_with_source_and_recorded_hashes(candidate):
    path, manifest = candidate
    assert release.inspect_artifacts(ROOT, path, manifest["source_commit"]) == manifest
    for artifact in manifest["artifacts"]:
        file = path / artifact["file"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == artifact["sha256"]


@pytest.fixture
def copy_candidate(candidate, tmp_path):
    original, manifest = candidate
    out = tmp_path / "changed"
    shutil.copytree(original, out)
    return out, manifest


def replace_zip(path, mutate):
    files = release.wheel_files(path)
    mutate(files)
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)


def replace_tar(path, mutate):
    files = release.tar_files(path)
    mutate(files)
    with tarfile.open(path, "w:gz") as archive:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))


@pytest.mark.parametrize("change", ["metadata", "source", "extra-module"])
def test_corrupted_wheel_rejected(copy_candidate, change):
    out, manifest = copy_candidate

    def corrupt(files):
        if change == "metadata":
            key = next(n for n in files if n.endswith(".dist-info/METADATA"))
            files[key] = files[key].replace(
                ("Version: " + manifest["sdk_version"]).encode(), b"Version: 99.0.0")
        elif change == "source":
            files["ccfoundry_agent_kit/_version.py"] += b"\n# unreviewed change\n"
        else:
            files["ccfoundry_agent_kit/unreviewed.py"] = b"print('unexpected')\n"

    replace_zip(next((out / "python").glob("*.whl")), corrupt)
    with pytest.raises(release.ReleaseError):
        release.inspect_artifacts(ROOT, out, manifest["source_commit"])


@pytest.mark.parametrize("kind", ["python", "npm"])
def test_archive_missing_license_rejected(copy_candidate, kind):
    out, manifest = copy_candidate
    path = next((out / kind).glob("*.tar.gz" if kind == "python" else "*.tgz"))

    def remove(files):
        for name in list(files):
            if name.endswith("/LICENSE"):
                del files[name]

    replace_tar(path, remove)
    with pytest.raises(release.ReleaseError):
        release.inspect_artifacts(ROOT, out, manifest["source_commit"])


@pytest.mark.parametrize("name", ["package/.env", "../escape.txt", "package/agent/.foundry_bootstrap.json"])
def test_npm_archive_private_or_unsafe_path_rejected(copy_candidate, name):
    out, manifest = copy_candidate
    replace_tar(next((out / "npm").glob("*.tgz")), lambda files: files.update({name: b"fixture-only"}))
    with pytest.raises(release.ReleaseError):
        release.inspect_artifacts(ROOT, out, manifest["source_commit"])


def test_npm_embedded_sdk_must_match_wheel(copy_candidate):
    out, manifest = copy_candidate
    replace_tar(next((out / "npm").glob("*.tgz")), lambda files: files.update({
        "package/packages/python-sdk/src/ccfoundry_agent_kit/_version.py": b'__version__ = "99.0.0"\n'
    }))
    with pytest.raises(release.ReleaseError, match="npm embedded SDK mismatch"):
        release.inspect_artifacts(ROOT, out, manifest["source_commit"])


def test_npm_missing_builtin_template_is_rejected(copy_candidate):
    out, manifest = copy_candidate
    replace_tar(next((out / "npm").glob("*.tgz")), lambda files: files.pop(
        "package/examples/verilog_module_writer/agent_space/config.yaml"))
    with pytest.raises(release.ReleaseError, match="npm built-in template missing"):
        release.inspect_artifacts(ROOT, out, manifest["source_commit"])


def test_npm_cli_installs_and_runs_without_lifecycle_scripts(candidate, tmp_path):
    path, manifest = candidate
    subprocess.run(["npm", "install", "--prefix", str(tmp_path), "--offline", "--ignore-scripts",
                    "--no-audit", "--no-fund", str(next((path / "npm").glob("*.tgz")))],
                   check=True, timeout=120, capture_output=True)
    entry = tmp_path / "node_modules/.bin/ccfoundry"
    result = subprocess.run([str(entry), "--version"], check=True, text=True, capture_output=True)
    assert result.stdout.strip() == manifest["npm_version"]
    help_result = subprocess.run([str(entry), "--help"], check=True, text=True, capture_output=True)
    assert "dev-board" in help_result.stdout and "--workspace" in help_result.stdout


def test_dirty_checkout_is_rejected_before_build_or_output_creation(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    source = root / "tracked.txt"
    source.write_text("original")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=release-fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    source.write_text("uncommitted")
    output = tmp_path / "output"
    with pytest.raises(release.ReleaseError, match="Commit tracked changes"):
        release.prepare(root, output)
    assert not output.exists()


def test_wheel_installs_in_fresh_venv_and_serves_local_chat(candidate, tmp_path):
    path, manifest = candidate
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=120)
    python = str(venv / "bin/python")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("AGENT_SECRET", None)
    for name, value in release.tar_files(next((path / "npm").glob("*.tgz"))).items():
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
    subprocess.run([python, "-m", "pip", "install", str(next((path / "python").glob("*.whl")))],
                   env=env, cwd=tmp_path, check=True, timeout=600, capture_output=True)
    subprocess.run([python, "-m", "pip", "check"], env=env, check=True, capture_output=True)
    code = '''
import asyncio, importlib.metadata, importlib.util, json, pathlib, sys
import httpx, ccfoundry_agent_kit as kit
assert pathlib.Path(kit.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert kit.__version__ == importlib.metadata.version("ccfoundry-agent-kit") == sys.argv[1]
async def reply(request, space):
    return kit.ChatResponse(agent="release-fixture", reply="installed: " + request.message)
app = kit.create_agent_app(manifest=kit.AgentManifest(name="release-fixture", label="Release fixture"),
                           chat_handler=reply, agent_space_dir="agent-space")
async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        health = await client.get("/health")
        assert health.status_code == 200
        response = await client.post("/chat", json={"message": "hello"})
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "installed: hello"
        card = await client.get("/.well-known/agent-card.json")
        assert card.status_code == 200
asyncio.run(run())
root = pathlib.Path.cwd()
package = root / "package"
sys.path.insert(0, str(package / "apps/agent-dev-board-api/src"))
from agent_dev_board_api.local_agents import LocalAgentManager
manager = LocalAgentManager(repo_root=package, runtime_dir=root / "runtime",
                                  agents_file=root / "agents.yaml", venv_python=pathlib.Path(sys.executable))
for template in ("me_agent", "verilog_module_writer"):
    item = manager.create_agent(template_id=template, name=template, start=False)
    assert item["status"] == "stopped" and item["pid"] is None
    assert (pathlib.Path(item["instance_dir"]) / "agent_space/SOUL.md").is_file()
    assert (pathlib.Path(item["instance_dir"]) / "agent_space/config.yaml").is_file()
print(json.dumps({"sdk_version": kit.__version__, "installed_under_venv": True,
                  "local_chat": "passed", "both_packaged_templates": "created_without_starting"}))
'''
    result = subprocess.run([python, "-c", code, manifest["sdk_version"]], env=env, cwd=tmp_path,
                            text=True, capture_output=True, check=True, timeout=60)
    assert json.loads(result.stdout)["local_chat"] == "passed"
    assert json.loads(result.stdout)["both_packaged_templates"] == "created_without_starting"
