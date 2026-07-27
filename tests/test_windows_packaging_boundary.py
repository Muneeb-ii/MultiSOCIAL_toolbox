from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_packaging_module(name: str):
    path = ROOT / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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


def test_windows_locks_have_disjoint_gui_and_native_ownership():
    gui = (ROOT / "requirements" / "locks" / "windows-gui-py310.txt").read_text(encoding="utf-8").lower()
    worker = (ROOT / "requirements" / "locks" / "windows-standard-py310.txt").read_text(encoding="utf-8").lower()

    for package in ("torch==", "transformers==", "mediapipe==", "opencv-contrib-python=="):
        assert package not in gui
        assert package in worker
    assert "wxpython==" in gui
    assert "wxpython==" not in worker
    assert "opencv-contrib-python==" in worker
    assert "opencv-python==" not in worker
    assert "opencv-python-headless==" not in worker
