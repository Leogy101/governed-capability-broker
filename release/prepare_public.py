"""Explicit source export only. Never creates a Git repository or publishes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tomllib


def export(root: Path, destination: Path, manifest: Path) -> dict:
    root = root.resolve()
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination_must_not_exist")
    if destination.resolve().is_relative_to(root):
        raise ValueError("destination_must_be_outside_source")
    entries = [line.strip() for line in manifest.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if not entries or len(entries) != len(set(entries)):
        raise ValueError("empty_or_duplicate_manifest")
    files = []
    for entry in entries:
        path = PurePosixPath(entry)
        if path.is_absolute() or ".." in path.parts or str(path) != entry:
            raise ValueError("invalid_manifest_path")
        if any(part == ".git" for part in path.parts):
            raise ValueError("git_metadata_forbidden")
        source = root.joinpath(*path.parts)
        if any(parent.is_symlink() for parent in (source, *source.parents) if parent.is_relative_to(root)):
            raise ValueError("symlink_forbidden")
        if not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError("missing_or_external_source")
        files.append((entry, source, source.read_bytes()))
    # Validate everything before creating the destination. No recursive copy or delete.
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for entry, source, data in files:
        target = destination / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        hashes[entry] = hashlib.sha256(data).hexdigest()
    revision = None
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and len(result.stdout.strip()) == 40:
            revision = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    project = {}
    if "pyproject.toml" in hashes:
        project = tomllib.loads((destination / "pyproject.toml").read_text()).get("project", {})
    declared_license = project.get("license")
    licensed = declared_license == "Apache-2.0" and {"LICENSE", "NOTICE"}.issubset(hashes)
    record = {"schema": "gcb.public-source/v1", "publication_authorized": False,
              "license_approved": licensed, "license_expression": declared_license,
              "candidate_version": project.get("version", "0.3.2a1"),
              "base_implementation_revision": "95174ac",
              "source_head_revision": revision,
              "provenance_note": "File hashes identify the export; source HEAD may precede uncommitted changes. License status reflects the declared project license and included notices. No Git history is included.",
              "files": hashes}
    (destination / "PUBLIC_SOURCE.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    record = export(root, args.output, root / "release/public-files.txt")
    print(json.dumps({"files": len(record["files"]), "publication_authorized": False}))


if __name__ == "__main__":
    main()
