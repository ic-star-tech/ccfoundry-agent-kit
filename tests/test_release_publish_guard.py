"""Guard and workflow checks for maintainer-authorized Python uploads."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_publish_guard", ROOT / "scripts/release_publish_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

VERSION = "9.8.7"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def release(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("source\n")
    git(repo, "add", "README.md")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "source")
    candidate = tmp_path / "candidate"
    artifacts = []
    for name in (f"python/ccfoundry_agent_kit-{VERSION}-py3-none-any.whl",
                 f"python/ccfoundry_agent_kit-{VERSION}.tar.gz", "npm/ccfoundry-1.0.0.tgz"):
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        artifacts.append({"file": name, "bytes": len(name), "sha256": hashlib.sha256(name.encode()).hexdigest()})
    manifest = {"status": "candidate", "published": False, "source_commit": git(repo, "rev-parse", "HEAD"),
                "sdk_version": VERSION, "artifacts": artifacts}
    (candidate / "release-manifest.json").write_text(json.dumps(manifest))
    git(repo, "tag", "python-v" + VERSION)
    return repo, candidate


def rewrite_manifest(candidate, **changes):
    path = candidate / "release-manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest))


def test_tagged_candidate_yields_only_python_artifacts(release):
    repo, candidate = release
    paths = guard.python_artifacts(repo, candidate, "python-v" + VERSION)
    assert [p.relative_to(candidate).as_posix() for p in paths] == [
        f"python/ccfoundry_agent_kit-{VERSION}-py3-none-any.whl",
        f"python/ccfoundry_agent_kit-{VERSION}.tar.gz",
    ]


def test_untagged_head_is_refused(release):
    repo, candidate = release
    git(repo, "tag", "-d", "python-v" + VERSION)
    with pytest.raises(guard.GuardError, match="release tag"):
        guard.python_artifacts(repo, candidate)


def test_tag_on_an_older_commit_is_refused(release):
    repo, candidate = release
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "later")
    rewrite_manifest(candidate, source_commit=git(repo, "rev-parse", "HEAD"))
    with pytest.raises(guard.GuardError, match="release tag"):
        guard.python_artifacts(repo, candidate)


def test_requested_tag_must_match_the_sdk_version(release):
    repo, candidate = release
    with pytest.raises(guard.GuardError, match="does not match"):
        guard.python_artifacts(repo, candidate, "python-v0.0.1")


def test_candidate_from_another_commit_is_refused(release):
    repo, candidate = release
    rewrite_manifest(candidate, source_commit="0" * 40)
    with pytest.raises(guard.GuardError, match="checked-out commit"):
        guard.python_artifacts(repo, candidate)


def test_published_manifest_is_refused(release):
    repo, candidate = release
    rewrite_manifest(candidate, published=True)
    with pytest.raises(guard.GuardError, match="unpublished candidate"):
        guard.python_artifacts(repo, candidate)


def test_artifact_changed_after_inspection_is_refused(release):
    repo, candidate = release
    (candidate / f"python/ccfoundry_agent_kit-{VERSION}.tar.gz").write_bytes(b"swapped")
    with pytest.raises(guard.GuardError, match="hash changed"):
        guard.python_artifacts(repo, candidate)


def test_extra_python_artifact_is_refused(release):
    repo, candidate = release
    manifest = json.loads((candidate / "release-manifest.json").read_text())
    extra = dict(manifest["artifacts"][0], file=f"python/ccfoundry_agent_kit-{VERSION}-cp310-any.whl")
    (candidate / extra["file"]).write_bytes(extra["file"].encode())
    extra["sha256"] = hashlib.sha256(extra["file"].encode()).hexdigest()
    rewrite_manifest(candidate, artifacts=manifest["artifacts"] + [extra])
    with pytest.raises(guard.GuardError, match="one wheel and one sdist"):
        guard.python_artifacts(repo, candidate)


@pytest.mark.parametrize("url", ["", "http://pypi.example/legacy/", "https://user:secret@pypi.example/legacy/",
                                 "https://pypi.example/legacy/?token=x", "upload.pypi.org/legacy/"])
def test_upload_index_must_be_explicit_https_without_credentials(url):
    with pytest.raises(guard.GuardError):
        guard.check_repository_url(url)


def test_explicit_https_index_is_accepted():
    guard.check_repository_url("https://upload.pypi.org/legacy/")


def test_release_workflow_is_manual_and_uploads_nothing_by_default():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert list(triggers) == ["workflow_dispatch"]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["publish"]["default"] is False
    assert inputs["repository_url"]["default"] == ""
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    uploads = [step for step in steps if "twine upload" in step.get("run", "")]
    assert len(uploads) == 1
    script = uploads[0]["run"]
    assert script.index('[ "$PUBLISH" != "true" ]') < script.index("release_publish_guard.py")
    assert script.index("release_publish_guard.py") < script.index("twine upload")
    assert "*" not in script.split("twine upload", 1)[1]
    assert "secrets." not in script
