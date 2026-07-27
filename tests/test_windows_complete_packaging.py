from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_hook_uses_a_stable_allowlist_without_recursive_discovery(tmp_path):
    hook = importlib.import_module("runtime_hook_dlls")
    (tmp_path / "torch" / "lib").mkdir(parents=True)
    (tmp_path / "mediapipe" / "python").mkdir(parents=True)
    (tmp_path / "unrelated" / "nested").mkdir(parents=True)

    directories = hook.bundled_dll_directories(str(tmp_path))

    assert directories[0] == str(tmp_path)
    assert str(tmp_path / "torch" / "lib") in directories
    assert str(tmp_path / "mediapipe" / "python") in directories
    assert str(tmp_path / "unrelated" / "nested") not in directories
    assert "os.walk" not in (ROOT / "src" / "runtime_hook_dlls.py").read_text(encoding="utf-8")

    mediapipe_phase = hook.bundled_dll_directories(str(tmp_path), include_tensor_runtime=False)
    assert str(tmp_path / "mediapipe" / "python") in mediapipe_phase
    assert str(tmp_path / "torch" / "lib") not in mediapipe_phase


def test_complete_smoke_and_diarization_use_the_windows_native_preload():
    app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")
    audio_source = (ROOT / "src" / "audio.py").read_text(encoding="utf-8")

    assert "runtime_services.preload_frozen_windows_diarization_dependencies()" in app_source
    assert "preload_frozen_windows_diarization_dependencies()" in audio_source


def test_windows_gui_uses_the_private_worker_without_path_mutation():
    app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")
    hook_source = (ROOT / "src" / "runtime_hook_dlls.py").read_text(encoding="utf-8")
    worker_source = (ROOT / "src" / "analysis_worker.py").read_text(encoding="utf-8")

    assert "WindowsAudioProcessor" in app_source
    assert "WindowsPoseProcessor" in app_source
    assert 'os.environ["PATH"]' not in hook_source
    assert "_configure_worker_native_loader" in worker_source
    assert "_enable_worker_tensor_loader" in worker_source


def test_windows_gui_does_not_statically_import_the_worker_native_graph():
    app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")
    spec_source = (ROOT / "MultiSOCIAL.spec").read_text(encoding="utf-8")

    assert "from audio import AudioProcessor" not in app_source
    assert "from pose import PoseProcessor" not in app_source
    assert 'importlib.import_module("audio").AudioProcessor' in app_source
    assert 'importlib.import_module("pose").PoseProcessor' in app_source
    assert '"audio",' in spec_source
    assert '"pose",' in spec_source


def test_windows_spec_builds_console_worker_and_workflow_probes_it():
    spec_source = (ROOT / "MultiSOCIAL.spec").read_text(encoding="utf-8")
    workflow_source = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'name="MultiSOCIAL-Worker"' in spec_source
    assert "console=True" in spec_source
    assert "gui_hiddenimports" in spec_source
    assert "worker_hiddenimports" in spec_source
    assert "gui_binaries = msvc_runtime_binaries" in spec_source
    assert 'gui_excludes = ["audio", "pose"]' in spec_source
    assert "prefix_collection_entries" in spec_source
    assert '"worker/MultiSOCIAL-Worker.exe"' in spec_source
    assert "merge_collection_entries(a.binaries, worker_a.binaries)" not in spec_source
    assert "Run packaged Windows analysis worker probe" in workflow_source
    assert "Run packaged Windows audio-feature worker smoke" in workflow_source
    assert "Worker probe ${attempt}/10" in workflow_source
    assert "Downloaded worker probe ${attempt}/10" in workflow_source
    assert "/worker/MultiSOCIAL-Worker.exe" in workflow_source
    assert "run_windows_complete_e2e" in workflow_source
    assert "MULTISOCIAL_CI_HF_TOKEN" in workflow_source


def test_worker_initializes_native_modules_before_starting_the_operation_thread():
    worker_source = (ROOT / "src" / "analysis_worker.py").read_text(encoding="utf-8")

    assert "_initialize_worker_operation_runtime(operation, payload)" in worker_source
    assert worker_source.index("_initialize_worker_operation_runtime(operation, payload)") < worker_source.index(
        "threading.Thread(target=run_operation, daemon=True).start()"
    )
