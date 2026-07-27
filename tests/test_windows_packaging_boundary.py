from __future__ import annotations

import ast
import importlib.util
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_packaging_module(name: str):
    path = ROOT / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_backend_contract_exposes_gui_factories_and_pose_lookup():
    source = (ROOT / "src" / "analysis_backend.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    backend = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AnalysisBackend")
    methods = {node.name for node in backend.body if isinstance(node, ast.FunctionDef)}

    assert {"create_audio_processor", "create_pose_processor", "find_pose_csv_paths"} <= methods


def test_windows_entry_reaches_only_worker_backend_and_shared_gui():
    source = (ROOT / "src" / "app_windows.py").read_text(encoding="utf-8")
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imported == {"analysis_backend", "worker_backend", "app"}
    assert "native_backend" not in source


def test_gui_graph_assertion_fails_before_assembly_on_native_module():
    support = _load_packaging_module("windows_build_support")
    analysis = SimpleNamespace(
        pure=[("torch", "site-packages/torch/__init__.py", "PYMODULE")],
        binaries=[],
        datas=[],
    )

    with pytest.raises(RuntimeError, match="native-analysis boundary"):
        support.assert_gui_graph(analysis)


def test_worker_graph_assertion_rejects_wxpython():
    support = _load_packaging_module("windows_build_support")
    analysis = SimpleNamespace(
        pure=[("wx.adv", "site-packages/wx/adv.py", "PYMODULE")],
        binaries=[],
        datas=[],
    )

    with pytest.raises(RuntimeError, match="wxPython"):
        support.assert_worker_graph(analysis)

def test_graph_assertions_reject_native_dll_and_wx_binary_names():
    support = _load_packaging_module("windows_build_support")
    gui = SimpleNamespace(
        pure=[],
        binaries=[("torch_cpu.dll", "site-packages/torch/lib/torch_cpu.dll", "BINARY")],
        datas=[],
    )
    worker = SimpleNamespace(
        pure=[],
        binaries=[("wxbase32u_vc140_x64.dll", "site-packages/wx/wxbase.dll", "BINARY")],
        datas=[],
    )

    with pytest.raises(RuntimeError, match="native-analysis boundary"):
        support.assert_gui_graph(gui)
    with pytest.raises(RuntimeError, match="wxPython"):
        support.assert_worker_graph(worker)

def test_vc_runtime_collection_selects_one_complete_directory(tmp_path, monkeypatch):
    support = _load_packaging_module("windows_build_support")
    assert support.VC_RUNTIME_NAMES == {
        "concrt140.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vcamp140.dll",
        "vccorlib140.dll",
        "vcomp140.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "vcruntime140_threads.dll",
    }
    package = tmp_path / "msvc_runtime"
    complete = package / "x64"
    package.mkdir()
    complete.mkdir()
    origin = package / "__init__.py"
    origin.touch()
    (package / "msvcp140.dll").touch()
    for name in support.VC_RUNTIME_NAMES:
        content = bytearray(0x86)
        content[:2] = b"MZ"
        content[0x3C:0x40] = struct.pack("<I", 0x80)
        content[0x80:0x84] = b"PE\0\0"
        content[0x84:0x86] = struct.pack("<H", support.PE_MACHINE_AMD64)
        (complete / name).write_bytes(content)
    monkeypatch.setattr(
        support,
        "find_spec",
        lambda _name: SimpleNamespace(origin=str(origin)),
    )
    monkeypatch.setattr(support.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(support.sys, "base_prefix", str(tmp_path / "python-root"))

    binaries = support.collect_vc_runtime_binaries()

    assert {Path(source).parent for source, _destination in binaries} == {complete}
    assert {Path(source).name.casefold() for source, _destination in binaries} == support.VC_RUNTIME_NAMES


def test_opaque_assembly_keeps_worker_runtime_under_worker_directory(tmp_path):
    assembler = _load_packaging_module("assemble_windows")
    gui = tmp_path / "gui"
    worker = tmp_path / "worker-build"
    output = tmp_path / "assembled"
    gui.mkdir()
    worker.mkdir()
    (gui / "MultiSOCIAL-Standard.exe").write_bytes(b"gui")
    (gui / "python310.dll").write_bytes(b"gui-python")
    (worker / "MultiSOCIAL-Worker.exe").write_bytes(b"worker")
    (worker / "python310.dll").write_bytes(b"worker-python")

    assembler.assemble(gui, worker, output)

    assert (output / "MultiSOCIAL-Standard.exe").read_bytes() == b"gui"
    assert (output / "worker" / "MultiSOCIAL-Worker.exe").read_bytes() == b"worker"
    assert (output / "python310.dll").read_bytes() != (output / "worker" / "python310.dll").read_bytes()

def test_assembly_rejects_output_that_can_delete_an_input(tmp_path):
    assembler = _load_packaging_module("assemble_windows")
    gui = tmp_path / "gui"
    worker = tmp_path / "worker"
    gui.mkdir()
    worker.mkdir()

    with pytest.raises(ValueError, match="must not contain"):
        assembler.assemble(gui, worker, tmp_path)

def test_runtime_root_rejects_nested_python_dll(tmp_path, monkeypatch):
    validator = _load_packaging_module("validate_windows_bundle")
    monkeypatch.setattr(
        validator,
        "_pe_machine",
        lambda _path: validator.PE_MACHINE_AMD64,
    )
    root = tmp_path / "runtime"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "app.exe").touch()
    (root / "python310.dll").touch()
    (nested / "python310.dll").touch()
    for name in validator.VC_RUNTIME_NAMES:
        (root / name).touch()

    with pytest.raises(RuntimeError, match="nested Python/VC runtime"):
        validator._assert_runtime_root(root, "app.exe")


def test_windows_locks_have_disjoint_gui_and_native_ownership():
    gui = (ROOT / "requirements" / "locks" / "windows-gui-py310.txt").read_text(encoding="utf-8").lower()
    worker = (ROOT / "requirements" / "locks" / "windows-standard-py310.txt").read_text(encoding="utf-8").lower()
    complete = (ROOT / "requirements" / "locks" / "windows-complete-py310.txt").read_text(encoding="utf-8").lower()

    for package in ("torch==", "transformers==", "mediapipe==", "opencv-contrib-python=="):
        assert package not in gui
        assert package in worker
    assert "wxpython==" in gui
    assert "wxpython==" not in worker
    assert "opencv-contrib-python==" in worker
    assert "opencv-python==" not in worker
    assert "opencv-python-headless==" not in worker
    assert "opencv-contrib-python==" in complete
    assert "opencv-python==" not in complete
    assert "opencv-python-headless==" not in complete

def test_source_windows_launcher_repairs_cv2_namespace_ownership():
    launcher = (ROOT / "run_app.bat").read_text(encoding="utf-8")

    assert "cv2-contrib-only-v1" in launcher
    assert "pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python" in launcher
    assert 'pip install "opencv-contrib-python==4.11.0.86"' in launcher
    assert "owners==['opencv-contrib-python']" in launcher


def test_worker_uses_checked_in_yolov5_inference_manifest():
    spec = (ROOT / "packaging" / "windows_worker.spec").read_text(encoding="utf-8")
    hook = (ROOT / "packaging" / "windows_hooks" / "hook-yolov5.py").read_text(encoding="utf-8")
    manifest = _load_packaging_module("windows_hiddenimports")

    assert "collect_submodules" not in spec
    assert "collect_submodules" not in hook
    assert {
        "yolov5.helpers",
        "yolov5.models.common",
        "yolov5.models.experimental",
        "yolov5.models.yolo",
        "yolov5.utils.general",
        "ultralytics.utils.checks",
    } <= set(manifest.YOLOV5_INFERENCE_HIDDEN_IMPORTS)
    assert not any(
        module.startswith(("ultralytics.models", "ultralytics.trackers", "yolov5.train"))
        for module in manifest.YOLOV5_INFERENCE_HIDDEN_IMPORTS
    )
    assert "sys.setrecursionlimit(max(sys.getrecursionlimit() * 5, 5000))" in spec


def test_yolo_autoinstall_is_disabled_during_build_and_runtime():
    spec = (ROOT / "packaging" / "windows_worker.spec").read_text(encoding="utf-8")
    runtime_hook = (ROOT / "src" / "runtime_hook_dlls.py").read_text(encoding="utf-8")
    ultralytics_hook = (ROOT / "packaging" / "windows_hooks" / "hook-ultralytics.py").read_text(encoding="utf-8")

    for variable in ("YOLO_AUTOINSTALL", "YOLOv5_AUTOINSTALL"):
        assert variable in spec
        assert variable in runtime_hook
    assert "ultralytics.trackers" in ultralytics_hook
    assert "ultralytics.models" in ultralytics_hook

def test_windows_transformers_hook_cannot_expand_beyond_whisper_manifest():
    spec = (ROOT / "packaging" / "windows_worker.spec").read_text(encoding="utf-8")
    hook = (ROOT / "packaging" / "windows_hooks" / "hook-transformers.py").read_text(encoding="utf-8")
    mac_hook = (ROOT / "hooks" / "hook-transformers.py").read_text(encoding="utf-8")

    assert 'WINDOWS_HOOKS = ROOT / "packaging" / "windows_hooks"' in spec
    assert "hookspath=[str(WINDOWS_HOOKS)]" in spec
    assert "collect_submodules" not in hook
    assert "TRANSFORMERS_WHISPER_HIDDEN_IMPORTS" in hook
    assert '"transformers.quantizers"' in hook
    assert "copy_metadata" in mac_hook

def test_speechbrain_runtime_discovery_matches_checked_in_complete_manifest():
    manifest = _load_packaging_module("windows_hiddenimports")
    path = ROOT / "src" / "runtime_hook_dlls.py"
    spec = importlib.util.spec_from_file_location("test_runtime_hook_dlls", path)
    assert spec is not None and spec.loader is not None
    runtime_hook = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(runtime_hook)
    finally:
        os.listdir = runtime_hook._original_listdir

    for module in manifest.COMPLETE_HIDDEN_IMPORTS:
        if module.startswith(("speechbrain.utils.", "speechbrain.dataio.", "speechbrain.nnet.")):
            assert any(
                f"{module.rsplit('.', 1)[1]}.py" in files
                for files in runtime_hook._WINDOWS_SPEECHBRAIN_FILES.values()
            )
    for unused in ("Accuracy.py", "autoencoders.py", "quantisers.py", "sampler.py"):
        assert not any(
            unused in files
            for files in runtime_hook._WINDOWS_SPEECHBRAIN_FILES.values()
        )
        assert any(
            unused in files
            for files in runtime_hook._NON_WINDOWS_SPEECHBRAIN_FILES.values()
        )
    assert {
        "pyannote.audio.models.embedding.wespeaker",
        "pyannote.audio.models.segmentation.PyanNet",
        "pyannote.audio.pipelines.speaker_verification",
        "speechbrain.lobes.features",
        "speechbrain.lobes.models.ECAPA_TDNN",
        "speechbrain.processing.features",
        "speechbrain.pretrained.interfaces",
    } <= set(manifest.COMPLETE_HIDDEN_IMPORTS)



def test_environment_audit_rejects_distributions_added_after_locked_install(tmp_path):
    audit = _load_packaging_module("audit_windows_environment")
    lock = tmp_path / "worker-lock.txt"
    lock.write_text(
        "example-package==1.2.3 \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"unexpected distributions: lap"):
        audit.assert_matches_lock(
            {"example-package": "1.2.3", "lap": "0.5.13", "pip": "25.0"},
            lock,
        )

    audit.assert_matches_lock({"example-package": "1.2.3", "pip": "25.0"}, lock)


def test_environment_audit_compares_versions_using_pep440_normalization(tmp_path):
    audit = _load_packaging_module("audit_windows_environment")
    lock = tmp_path / "worker-lock.txt"
    lock.write_text(
        "thop==0.1.1.post2209072238 \\\n"
        "    --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )

    audit.assert_matches_lock(
        {"thop": "0.1.1-2209072238", "pip": "25.0"},
        lock,
    )

    with pytest.raises(RuntimeError, match=r"thop==0\.1\.2"):
        audit.assert_matches_lock({"thop": "0.1.2", "pip": "25.0"}, lock)


def test_environment_audit_rejects_unhashed_and_duplicate_lock_entries(tmp_path):
    audit = _load_packaging_module("audit_windows_environment")
    unhashed = tmp_path / "unhashed.txt"
    unhashed.write_text("example_package==1.0\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing a SHA256 hash"):
        audit.locked_versions(unhashed)

    duplicate = tmp_path / "duplicate.txt"
    duplicate.write_text(
        "example-package==1.0 \\\n"
        "  --hash=sha256:" + "0" * 64 + "\n"
        "example_package==1.0 \\\n"
        "  --hash=sha256:" + "1" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Duplicate locked requirement"):
        audit.locked_versions(duplicate)


def test_environment_audit_checks_manifest_paths_without_importing_packages(tmp_path):
    audit = _load_packaging_module("audit_windows_environment")
    package = tmp_path / "example" / "runtime"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "standalone.py").write_text("", encoding="utf-8")

    missing = audit.missing_manifest_modules(
        ["example.runtime", "standalone", "missing.module"],
        [tmp_path],
    )

    assert missing == ["missing.module"]
