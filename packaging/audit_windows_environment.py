"""Audit installed distributions before either Windows PyInstaller process runs."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
from pathlib import Path


NATIVE_ANALYSIS_DISTRIBUTIONS = {
    "mediapipe",
    "opensmile",
    "opencv-contrib-python",
    "opencv-python",
    "opencv-python-headless",
    "pyannote-audio",
    "speechbrain",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "yolov5",
}


def canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def installed_versions() -> dict[str, str]:
    inventory: dict[str, list[str]] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        inventory.setdefault(canonicalize_name(name.strip()), []).append(distribution.version)
    duplicates = {
        name: versions
        for name, versions in inventory.items()
        if len(versions) != 1
    }
    if duplicates:
        details = ", ".join(
            f"{name}={versions!r}" for name, versions in sorted(duplicates.items())
        )
        raise RuntimeError("Duplicate installed distributions detected: " + details)
    return {name: versions[0] for name, versions in inventory.items()}


def locked_versions(lock_path: Path) -> dict[str, str]:
    locked: dict[str, str] = {}
    requirement = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")
    lines = lock_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = requirement.match(line)
        if not match:
            continue
        name = canonicalize_name(match.group(1))
        if name in locked:
            raise RuntimeError(f"Duplicate locked requirement: {name}")
        block_end = next(
            (
                following
                for following in range(index + 1, len(lines))
                if requirement.match(lines[following])
            ),
            len(lines),
        )
        if "--hash=sha256:" not in "\n".join(lines[index:block_end]):
            raise RuntimeError(f"Locked requirement is missing a SHA256 hash: {name}")
        locked[name] = match.group(2)
    if not locked:
        raise RuntimeError(f"No pinned requirements found in lock: {lock_path}")
    return locked


def assert_matches_lock(installed: dict[str, str], lock_path: Path) -> None:
    locked = locked_versions(lock_path)
    # pip is created by venv and is intentionally not part of application locks.
    unexpected = sorted(set(installed) - set(locked) - {"pip"})
    missing = sorted(set(locked) - set(installed))
    mismatched = sorted(
        f"{name}=={installed[name]} (locked {locked[name]})"
        for name in set(installed) & set(locked)
        if installed[name] != locked[name]
    )
    failures = []
    if unexpected:
        failures.append("unexpected distributions: " + ", ".join(unexpected))
    if missing:
        failures.append("missing distributions: " + ", ".join(missing))
    if mismatched:
        failures.append("version mismatches: " + ", ".join(mismatched))
    if failures:
        raise RuntimeError("Installed environment differs from hashed lock; " + "; ".join(failures))


def audit(kind: str, lock_path: Path | None = None) -> None:
    installed = installed_versions()
    if lock_path is not None:
        assert_matches_lock(installed, lock_path)
    names = set(installed)
    if kind == "gui":
        hits = sorted(names & NATIVE_ANALYSIS_DISTRIBUTIONS)
        if hits:
            raise RuntimeError("Native analysis distributions installed in GUI environment: " + ", ".join(hits))
        if "wxpython" not in names:
            raise RuntimeError("GUI environment is missing wxPython")
        return

    if "wxpython" in names:
        raise RuntimeError("Worker environment contains wxPython")
    providers = sorted(
        name for name in names
        if name in {"opencv-contrib-python", "opencv-python", "opencv-python-headless"}
    )
    if providers != ["opencv-contrib-python"]:
        raise RuntimeError("Worker must have exactly one cv2 provider (opencv-contrib-python): " + repr(providers))
    cv2_owners = sorted(
        canonicalize_name(name)
        for name in importlib.metadata.packages_distributions().get("cv2", [])
    )
    if cv2_owners != ["opencv-contrib-python"]:
        raise RuntimeError("cv2 namespace ownership is ambiguous: " + repr(cv2_owners))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("gui", "worker"), required=True)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    audit(args.kind, args.lock)
    print(f"Windows {args.kind} environment ownership audit passed.")


if __name__ == "__main__":
    main()
