from __future__ import annotations

from pathlib import Path


def test_captions_module_is_packaged():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    assert '"captions",' in text


def test_yolov5_runtime_dependencies_are_packaged():
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "packaging" / "windows_worker.spec").read_text(encoding="utf-8")
    hook_text = (root / "packaging" / "windows_hooks" / "hook-yolov5.py").read_text(encoding="utf-8")
    ultralytics_hook = (root / "packaging" / "windows_hooks" / "hook-ultralytics.py").read_text(encoding="utf-8")
    manifest_text = (root / "packaging" / "windows_hiddenimports.py").read_text(encoding="utf-8")

    assert '"ultralytics",' in spec_text
    assert "YOLOV5_INFERENCE_HIDDEN_IMPORTS" in spec_text
    assert 'collect_submodules("ultralytics")' not in spec_text
    assert '"torchvision",' in spec_text
    assert 'ROOT / "assets" / "yolov5s.pt"' in spec_text

    assert 'collect_submodules("ultralytics")' not in hook_text
    assert '"yolov5", "ultralytics", "torch", "torchvision"' in hook_text
    assert 'collect_data_files("ultralytics", include_py_files=False)' in ultralytics_hook
    assert '"ultralytics.trackers"' in ultralytics_hook
    assert '"yolov5.models.yolo"' in manifest_text


def test_mediapipe_heavy_pose_model_is_packaged():
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "packaging" / "windows_worker.spec").read_text(encoding="utf-8")

    assert 'ROOT / "assets" / "pose_landmark_heavy.tflite"' in spec_text
    assert 'os.path.join("mediapipe", "modules", "pose_landmark")' in spec_text


def test_macos_bundle_uses_project_version_metadata():
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "MultiSOCIAL.spec").read_text(encoding="utf-8")

    assert '"CFBundleShortVersionString": APP_VERSION' in spec_text
    assert '"CFBundleVersion": APP_VERSION' in spec_text
