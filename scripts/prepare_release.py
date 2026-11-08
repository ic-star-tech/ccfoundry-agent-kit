#!/usr/bin/env python3
"""Build and inspect release candidates from one committed tree; never publish."""

from __future__ import annotations

import argparse
import ast
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import zipfile


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sdk_version(source):
    tree = ast.parse(source)
    versions = [ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__version__"
                        for t in node.targets)]
    require(len(versions) == 1 and isinstance(versions[0], str), "Invalid SDK version source")
    return versions[0]


def safe_name(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in name,
            "Unsafe archive path")
    banned = {".git", ".venv", "node_modules", "__pycache__", ".dev-board"}
    require(not banned.intersection(path.parts), "Runtime or checkout data in artifact")
    base = path.name.lower()
    require(not (base.startswith(".env") and base != ".env.example"), "Environment file in artifact")
    require(not base.endswith((".pyc", ".pem", ".key")), "Private key or bytecode in artifact")
    require(not (base.endswith(".json") and any(
        marker in base for marker in ("credentials", "client_secret", "service-account", ".foundry_bootstrap")
    )), "Credential or bootstrap state in artifact")


def tar_files(path):
    files = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            safe_name(member.name)
            require(member.isdir() or member.isfile(), "Archive links are not allowed")
            if member.isfile():
                require(member.name not in files, "Duplicate archive member")
                files[member.name] = archive.extractfile(member).read()
    return files


def wheel_files(path):
    files = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            safe_name(member.filename)
            require(member.filename not in files, "Duplicate wheel member")
            if not member.is_dir():
                files[member.filename] = archive.read(member)
    return files


def single(items, description):
    items = list(items)
    require(len(items) == 1, "Expected one " + description)
    return items[0]


def inspect_artifacts(snapshot, output, commit):
    """Compare all packaged SDK source bytes with the committed source snapshot."""
    package = json.loads((snapshot / "package.json").read_text())
    sdk = snapshot / "packages/python-sdk"
    version = sdk_version((sdk / "src/ccfoundry_agent_kit/_version.py").read_text())
    license_text = (snapshot / "LICENSE").read_bytes()
    require((sdk / "LICENSE").read_bytes() == license_text, "SDK license differs from root license")
    wheel = single((output / "python").glob("*.whl"), "wheel")
    sdist = single((output / "python").glob("*.tar.gz"), "sdist")
    npm = single((output / "npm").glob("*.tgz"), "npm tarball")
    whl, src, node = wheel_files(wheel), tar_files(sdist), tar_files(npm)
    metadata_path = single((n for n in whl if n.endswith(".dist-info/METADATA")), "wheel metadata")
    metadata = BytesParser().parsebytes(whl[metadata_path])
    require(metadata["Name"] == "ccfoundry-agent-kit" and metadata["Version"] == version,
            "Wheel name/version mismatch")
    prefix = single({PurePosixPath(n).parts[0] for n in src}, "sdist root") + "/"
    sdist_metadata = BytesParser().parsebytes(src[prefix + "PKG-INFO"])
    require(sdist_metadata["Name"] == metadata["Name"] and sdist_metadata["Version"] == version,
            "Sdist name/version mismatch")
    for key in ("Requires-Python", "Requires-Dist"):
        require(metadata.get_all(key) == sdist_metadata.get_all(key), "Wheel/sdist requirements differ")
    require(any(n.endswith("/LICENSE") and b == license_text for n, b in whl.items()),
            "Wheel must carry the full MIT license")
    require(src.get(prefix + "LICENSE") == license_text, "Sdist must carry the full MIT license")
    require(node.get("package/LICENSE") == license_text, "npm tarball must carry the full MIT license")
    node_package = json.loads(node["package/package.json"])
    require(node_package["name"] == package["name"] and node_package["version"] == package["version"],
            "npm name/version mismatch")
    sources = sorted((sdk / "src/ccfoundry_agent_kit").rglob("*.py"))
    source_names = {p.relative_to(sdk / "src").as_posix() for p in sources}
    require({n for n in whl if n.endswith(".py")} == source_names,
            "Unexpected or missing Python module in wheel")
    for source in sources:
        relative = source.relative_to(sdk / "src").as_posix()
        expected = source.read_bytes()
        require(whl.get(relative) == expected, "Wheel source mismatch: " + relative)
        require(src.get(prefix + "src/" + relative) == expected, "Sdist source mismatch: " + relative)
        require(node.get("package/packages/python-sdk/src/" + relative) == expected,
                "npm embedded SDK mismatch: " + relative)
    required_node = ["scripts/dev-board.mjs", "packages/python-sdk/pyproject.toml",
                     "packages/python-sdk/README.md", "packages/python-sdk/LICENSE",
                     "apps/agent-dev-board-api/pyproject.toml", "apps/agent-dev-board-api/README.md",
                     "examples/me_agent/pyproject.toml", "examples/me_agent/README.md",
                     "apps/agent-dev-board-web/package-lock.json", "CHANGELOG.md", "docs/releases.md"]
    for name in required_node:
        require(node.get("package/" + name) == (snapshot / name).read_bytes(),
                "npm launcher/document dependency missing or changed: " + name)
    template_files = []
    for template in ("me_agent", "verilog_module_writer"):
        for file in sorted((snapshot / "examples" / template / "agent_space").rglob("*")):
            if file.is_file():
                name = file.relative_to(snapshot).as_posix()
                require(node.get("package/" + name) == file.read_bytes(),
                        "npm built-in template missing or changed: " + name)
                template_files.append(name)
    artifacts = [{"file": p.relative_to(output).as_posix(), "bytes": p.stat().st_size,
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (wheel, sdist, npm)]
    return {"schema_version": 1, "source_commit": commit, "status": "candidate",
            "published": False, "sdk_version": version, "npm_version": package["version"],
            "proposed_tags": ["python-v" + version, "npm-v" + package["version"]],
            "sdk_source_files_checked": len(sources), "artifacts": artifacts,
            "template_files_checked": len(template_files),
            "compatibility": "See docs/releases.md; no certified Foundry version range yet",
            "requires_python": metadata["Requires-Python"],
            "requires_dist": metadata.get_all("Requires-Dist", []),
            "limitations": ["Not a secret-content scan", "No public-index installation or live Foundry test",
                            "Version policy, compatibility evidence and publication authorization remain release gates"]}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def prepare(root, output):
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "Commit tracked changes before building a release candidate")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    require(not output.exists(), "Output path already exists; choose a new directory")
    output.mkdir(parents=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["SOURCE_DATE_EPOCH"] = git(root, "show", "-s", "--format=%ct", commit).decode().strip()
    with tempfile.TemporaryDirectory(prefix="ccfoundry-release-") as temporary:
        snapshot = Path(temporary) / "source"
        snapshot.mkdir()
        archive_bytes = git(root, "archive", "--format=tar", commit)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
            # Only committed regular files/directories; never copy worktree state.
            for member in archive.getmembers():
                require(member.isfile() or member.isdir(), "Source links are not allowed")
                path = PurePosixPath(member.name)
                require(not path.is_absolute() and ".." not in path.parts, "Unsafe source path")
            archive.extractall(snapshot)
        require(git(root, "rev-parse", "HEAD").decode().strip() == commit,
                "HEAD changed while preparing the snapshot")
        (output / "python").mkdir()
        (output / "npm").mkdir()
        subprocess.run([sys.executable, "-m", "build", str(snapshot / "packages/python-sdk"),
                        "--outdir", str(output / "python")], env=env, check=True, timeout=900)
        subprocess.run(["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(output / "npm")],
                       cwd=snapshot, env=env, check=True, timeout=300, stdout=subprocess.DEVNULL)
        manifest = inspect_artifacts(snapshot, output, commit)
        subprocess.run([sys.executable, "-m", "twine", "check", "--strict",
                        *[str(output / a["file"]) for a in manifest["artifacts"][:2]]],
                       env=env, check=True, timeout=120)
        (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory (not overwritten)")
    args = parser.parse_args()
    try:
        prepare(Path(__file__).resolve().parents[1], args.output.resolve())
    except (ReleaseError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Release candidate failed: {error}\nNo publication was attempted.\n")


if __name__ == "__main__":
    main()
