"""Post-assembly ownership checks for the two Windows onedir runtimes."""

from __future__ import annotations

import argparse
from pathlib import Path

from windows_build_support import VC_RUNTIME_NAMES


def _runtime_files(root: Path, excluded: Path | None = None) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if excluded is not None and (path == excluded or excluded in path.parents):
            continue
        files.append(path)
    return files


def _assert_runtime_root(root: Path, executable: str, excluded: Path | None = None) -> None:
    if not (root / executable).is_file():
        raise RuntimeError(f"Missing runtime executable: {root / executable}")
    python_dlls = list(root.glob("python3*.dll"))
    if len(python_dlls) != 1:
        raise RuntimeError(f"Expected one Python runtime DLL at {root}, found {len(python_dlls)}")
    missing_vc = sorted(name for name in VC_RUNTIME_NAMES if not (root / name).is_file())
    if missing_vc:
        raise RuntimeError(f"Missing VC runtime files at {root}: {', '.join(missing_vc)}")
    duplicates = [
        path.relative_to(root)
        for path in _runtime_files(root, excluded)
        if path.name.casefold() in VC_RUNTIME_NAMES and path.parent != root
    ]
    if duplicates:
        raise RuntimeError(f"Duplicate nested VC runtime files under {root}: {duplicates[:10]}")


def validate(root: Path, profile: str) -> None:
    app_name = "MultiSOCIAL-Complete" if profile == "complete" else "MultiSOCIAL-Standard"
    worker = root / "worker"
    _assert_runtime_root(root, f"{app_name}.exe", excluded=worker)
    _assert_runtime_root(worker, "MultiSOCIAL-Worker.exe")

    forbidden_gui_parts = {
        "audio.py",
        "cv2",
        "mediapipe",
        "opensmile",
        "pose.py",
        "pyannote",
        "speechbrain",
        "torch",
        "torchaudio",
        "transformers",
        "ultralytics",
        "yolov5",
    }
    gui_hits = [
        str(path.relative_to(root))
        for path in _runtime_files(root, excluded=worker)
        if any(part.casefold() in forbidden_gui_parts for part in path.parts)
    ]
    if gui_hits:
        raise RuntimeError("GUI runtime contains worker-owned files: " + ", ".join(gui_hits[:20]))
    if any(part.name.casefold() == "wx" for part in worker.rglob("*") if part.is_dir()):
        raise RuntimeError("Worker runtime contains wxPython")

    required_worker_paths = [
        "audresample/core/bin/win_amd64/audresample.dll",
        "opensmile/core/bin/win_amd64/SMILEapi.dll",
        "mediapipe/modules/pose_landmark/pose_landmark_heavy.tflite",
    ]
    missing = [path for path in required_worker_paths if not (worker / path).is_file()]
    if missing:
        raise RuntimeError("Worker-native files missing: " + ", ".join(missing))
    if not list(worker.rglob("_framework_bindings*.pyd")):
        raise RuntimeError("Worker MediaPipe binding is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--profile", choices=("standard", "complete"), required=True)
    args = parser.parse_args()
    validate(args.root.resolve(), args.profile)
    print("Windows GUI/worker runtime boundary validation passed.")


if __name__ == "__main__":
    main()
