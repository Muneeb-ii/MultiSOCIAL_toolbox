# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


SPEC_DIR = Path(globals().get("SPECPATH", Path.cwd() / "packaging")).resolve()
ROOT = SPEC_DIR.parent if SPEC_DIR.name == "packaging" else SPEC_DIR
SRC = ROOT / "src"
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(ROOT / "packaging"))

from windows_build_support import (  # noqa: E402
    approved_vc_sources,
    assert_worker_graph,
    collect_vc_runtime_binaries,
    filter_vc_runtime_entries,
)
from windows_hiddenimports import COMPLETE_HIDDEN_IMPORTS, STANDARD_HIDDEN_IMPORTS  # noqa: E402

profile = os.environ.get("MULTISOCIAL_BUILD_PROFILE", "standard").strip().lower()
if profile not in {"standard", "complete"}:
    raise RuntimeError(f"Unsupported Windows worker profile: {profile}")

hiddenimports = list(STANDARD_HIDDEN_IMPORTS)
# YOLOv5 7.0.14 uses filesystem-driven module discovery. Keep this isolated
# to the worker process; the native-free GUI environment does not install it.
hiddenimports += collect_submodules("yolov5")
hiddenimports += collect_submodules("ultralytics")

datas = [
    (str(ROOT / "assets" / "yolov5s.pt"), "assets"),
    (
        str(ROOT / "assets" / "pose_landmark_heavy.tflite"),
        os.path.join("mediapipe", "modules", "pose_landmark"),
    ),
    (str(ROOT / "env.example"), "."),
    (str(ROOT / "pyproject.toml"), "."),
]
for package in (
    "audinterface",
    "audresample",
    "imageio_ffmpeg",
    "lightning_fabric",
    "mediapipe",
    "opensmile",
    "ultralytics",
    "yolov5",
):
    datas += collect_data_files(package)
for package in (
    "torch",
    "torchvision",
    "transformers",
    "ultralytics",
    "yolov5",
):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

binaries = []
for package in ("audinterface", "audresample", "mediapipe", "opensmile", "soundfile"):
    binaries += collect_dynamic_libs(package)

if profile == "complete":
    hiddenimports += COMPLETE_HIDDEN_IMPORTS
    for package in (
        "huggingface_hub",
        "pyannote",
        "pytorch_lightning",
        "speechbrain",
        "torchaudio",
    ):
        datas += collect_data_files(package)
    for package in (
        "huggingface_hub",
        "lightning",
        "pyannote.audio",
        "pyannote.core",
        "pytorch_lightning",
        "speechbrain",
        "torchaudio",
        "torchmetrics",
    ):
        try:
            datas += copy_metadata(package)
        except Exception:
            pass

vc_binaries = collect_vc_runtime_binaries()
approved_sources = approved_vc_sources(vc_binaries)
binaries += vc_binaries

a = Analysis(
    [str(SRC / "analysis_worker.py")],
    pathex=[str(ROOT), str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(HOOKS)],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[str(SRC / "runtime_hook_dlls.py")],
    excludes=["wx", "transformers.quantizers"],
    noarchive=False,
)
a.binaries = filter_vc_runtime_entries(a.binaries, approved_sources)
a.datas = filter_vc_runtime_entries(a.datas, approved_sources)
assert_worker_graph(a)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MultiSOCIAL-Worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory=".",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MultiSOCIAL-Worker",
)
