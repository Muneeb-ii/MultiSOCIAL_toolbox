"""Audit installed distributions before either Windows PyInstaller process runs."""

from __future__ import annotations

import argparse
import importlib.metadata


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


def installed_names() -> set[str]:
    return {
        distribution.metadata["Name"].strip().casefold()
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }


def audit(kind: str) -> None:
    names = installed_names()
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
        name.casefold().replace("_", "-")
        for name in importlib.metadata.packages_distributions().get("cv2", [])
    )
    if cv2_owners != ["opencv-contrib-python"]:
        raise RuntimeError("cv2 namespace ownership is ambiguous: " + repr(cv2_owners))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("gui", "worker"), required=True)
    args = parser.parse_args()
    audit(args.kind)
    print(f"Windows {args.kind} environment ownership audit passed.")


if __name__ == "__main__":
    main()
