"""Shared helpers for the two physically independent Windows PyInstaller specs."""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Iterable


VC_RUNTIME_NAMES = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
GUI_FORBIDDEN_MODULE_ROOTS = {
    "analysis_worker",
    "audio",
    "audresample",
    "cv2",
    "mediapipe",
    "opensmile",
    "pose",
    "pyannote",
    "speechbrain",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "ultralytics",
    "yolov5",
}
GUI_FORBIDDEN_PATH_PARTS = GUI_FORBIDDEN_MODULE_ROOTS | {
    "numpy.libs",
    "opencv",
    "openmp",
}


def _normalized_source(entry) -> str | None:
    if not isinstance(entry, tuple) or not entry:
        return None
    value = entry[1] if len(entry) >= 3 else entry[0]
    return os.path.normcase(os.path.abspath(str(value)))


def collect_vc_runtime_binaries() -> list[tuple[str, str]]:
    """Collect exactly one approved VC runtime set from the build environment."""
    candidate_groups: list[list[Path]] = []
    spec = find_spec("msvc_runtime")
    msvc_root: Path | None = None
    if spec is not None and spec.origin:
        origin = Path(spec.origin)
        msvc_root = origin.parent if origin.is_file() else origin
        candidate_groups.append(
            [path for path in msvc_root.glob("*.dll") if path.name.casefold() in VC_RUNTIME_NAMES]
        )
    python_roots = list(dict.fromkeys([Path(sys.executable).resolve().parent, Path(sys.base_prefix)]))
    for root in python_roots:
        for directory in (root, root / "DLLs", root / "Library" / "bin"):
            if directory.is_dir():
                candidate_groups.append(
                    [
                        path for path in directory.iterdir()
                        if path.is_file() and path.name.casefold() in VC_RUNTIME_NAMES
                    ]
                )
    if msvc_root is not None:
        bad_architecture_parts = {"arm", "arm64", "win32", "x86"}
        recursive = [
            path
            for path in msvc_root.rglob("*")
            if path.is_file()
            and path.name.casefold() in VC_RUNTIME_NAMES
            and not ({part.casefold() for part in path.parts} & bad_architecture_parts)
        ]
        candidate_groups.append(sorted(recursive, key=lambda path: (len(path.parts), str(path).casefold())))

    selected: dict[str, Path] = {}
    for candidates in candidate_groups:
        for candidate in candidates:
            selected.setdefault(candidate.name.casefold(), candidate)
    missing = sorted(VC_RUNTIME_NAMES - set(selected))
    if missing:
        raise RuntimeError("VC runtime files missing from isolated build environment: " + ", ".join(missing))
    return [(str(selected[name]), ".") for name in sorted(selected)]


def filter_vc_runtime_entries(entries: Iterable[tuple], approved_sources: set[str]) -> list[tuple]:
    filtered = []
    for entry in entries:
        if not isinstance(entry, tuple) or not entry:
            filtered.append(entry)
            continue
        destination = str(entry[0]).replace("\\", "/")
        basename = os.path.basename(destination).casefold()
        source = _normalized_source(entry)
        if basename in VC_RUNTIME_NAMES:
            if os.path.dirname(destination) not in ("", ".") or source not in approved_sources:
                continue
        filtered.append(entry)
    return filtered


def approved_vc_sources(entries: Iterable[tuple]) -> set[str]:
    return {
        source
        for source in (_normalized_source(entry) for entry in entries)
        if source
    }


def _module_names(entries: Iterable[tuple]) -> set[str]:
    return {str(entry[0]) for entry in entries if isinstance(entry, tuple) and entry}


def _entry_text(entry: tuple) -> str:
    return " | ".join(str(value).replace("\\", "/").casefold() for value in entry)


def assert_gui_graph(analysis) -> None:
    module_hits = sorted(
        name
        for name in _module_names(analysis.pure)
        if name.split(".", 1)[0].casefold() in GUI_FORBIDDEN_MODULE_ROOTS
    )
    native_hits = []
    for entry in [*analysis.binaries, *analysis.datas]:
        text = _entry_text(entry)
        if any(f"/{part}/" in f"/{text}/" or f"/{part}." in f"/{text}" for part in GUI_FORBIDDEN_PATH_PARTS):
            native_hits.append(str(entry[0]))
    if module_hits or native_hits:
        details = [*(f"module:{name}" for name in module_hits), *(f"file:{name}" for name in native_hits)]
        raise RuntimeError("Windows GUI graph crossed the native-analysis boundary: " + ", ".join(details[:30]))


def assert_worker_graph(analysis) -> None:
    wx_hits = sorted(name for name in _module_names(analysis.pure) if name.split(".", 1)[0].casefold() == "wx")
    if wx_hits:
        raise RuntimeError("Windows worker graph contains wxPython: " + ", ".join(wx_hits[:20]))
