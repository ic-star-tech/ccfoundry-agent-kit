#!/usr/bin/env python3
"""Check an inspected candidate before a maintainer-authorized Python upload.

This helper never uploads. It prints the exact wheel and sdist paths recorded in
``release-manifest.json`` after verifying that they belong to the checked-out
commit, match their recorded hashes, and that HEAD carries the release tag
``python-v<sdk_version>``. The release workflow passes those paths, and only
those paths, to ``twine upload``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from urllib.parse import urlsplit


class GuardError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GuardError(message)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True)


def check_repository_url(url):
    parts = urlsplit(url or "")
    require(parts.scheme == "https" and parts.hostname, "Upload index must be an explicit https URL")
    require(not parts.username and not parts.password, "Credentials must not be embedded in the index URL")
    require(not parts.query and not parts.fragment, "Index URL must not carry a query or fragment")


def python_artifacts(root, candidate, expected_tag=None):
    """Return the verified wheel and sdist paths, in manifest order."""
    manifest = json.loads((candidate / "release-manifest.json").read_text())
    require(manifest.get("status") == "candidate" and manifest.get("published") is False,
            "Manifest is not an unpublished candidate")
    head = git(root, "rev-parse", "HEAD").strip()
    require(manifest.get("source_commit") == head, "Candidate was not built from the checked-out commit")
    version = manifest.get("sdk_version")
    require(isinstance(version, str) and version, "Manifest has no SDK version")
    tag = "python-v" + version
    require(expected_tag in (None, tag), f"Requested tag {expected_tag} does not match {tag}")
    require(tag in git(root, "tag", "--points-at", "HEAD").split(),
            f"HEAD must carry the release tag {tag}; create it on the reviewed commit first")
    paths = []
    for artifact in manifest.get("artifacts", []):
        name = PurePosixPath(artifact["file"])
        if name.parts[:1] != ("python",):
            continue
        require(len(name.parts) == 2 and not name.is_absolute(), "Unexpected artifact path")
        path = candidate / name
        require(path.is_file(), "Missing candidate artifact: " + artifact["file"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == artifact["sha256"], "Artifact hash changed after inspection: " + artifact["file"])
        paths.append(path)
    wheels = [p for p in paths if p.name.endswith(".whl")]
    sdists = [p for p in paths if p.name.endswith(".tar.gz")]
    require(len(paths) == 2 and len(wheels) == 1 and len(sdists) == 1,
            "Expected exactly one wheel and one sdist")
    prefix = "ccfoundry_agent_kit-" + version
    require(all(p.name.startswith(prefix) for p in paths), "Artifact names do not match the SDK version")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="Directory from prepare_release.py")
    parser.add_argument("--repository-url", required=True, help="Maintainer-selected upload index")
    parser.add_argument("--tag", help="Optional expected tag, e.g. python-v0.1.0")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        check_repository_url(args.repository_url)
        for path in python_artifacts(root, args.candidate.resolve(), args.tag):
            print(path)
    except (GuardError, OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release guard failed: {error}\nNothing was uploaded.\n")


if __name__ == "__main__":
    main()
