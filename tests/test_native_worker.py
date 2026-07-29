from __future__ import annotations

import os
import sys
import threading
import time

import pytest


def _worker_script(tmp_path, source: str):
    path = tmp_path / "worker.py"
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


def test_worker_client_forwards_protocol_progress_and_result(tmp_path):
    from native_worker_client import NativeWorkerClient

    command = _worker_script(
        tmp_path,
        """import json, sys
request = json.loads(sys.stdin.readline())
base = {\"protocol\": 1, \"id\": request[\"id\"]}
print(json.dumps({**base, \"event\": \"progress\", \"value\": 40}), flush=True)
print(json.dumps({**base, \"event\": \"result\", \"result\": {\"succeeded\": [\"ok\"], \"failed\": [], \"cancelled\": False}}), flush=True)
""",
    )
    updates = []
    result = NativeWorkerClient(command=command).run("probe", {}, progress_callback=updates.append)

    assert updates == [40]
    assert result == {"succeeded": ["ok"], "failed": [], "cancelled": False}


def test_worker_client_redacts_child_token_in_errors(tmp_path):
    from native_worker_client import NativeWorkerClient, WorkerError

    command = _worker_script(
        tmp_path,
        """import json, os, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({\"protocol\": 1, \"id\": request[\"id\"], \"event\": \"error\", \"message\": os.environ[\"MULTISOCIAL_WORKER_HF_TOKEN\"]}), flush=True)
""",
    )

    with pytest.raises(WorkerError, match="REDACTED") as exc:
        NativeWorkerClient(command=command).run("probe", {}, hf_token="hf-secret")
    assert "hf-secret" not in str(exc.value)


def test_worker_client_cancels_unresponsive_child(tmp_path):
    from native_worker_client import NativeWorkerClient

    command = _worker_script(
        tmp_path,
        """import json, sys, time
json.loads(sys.stdin.readline())
time.sleep(30)
""",
    )
    cancelled = threading.Event()
    cancelled.set()
    started = time.monotonic()
    result = NativeWorkerClient(command=command, cancel_grace_seconds=0.05).run(
        "probe", {}, cancel_check=cancelled.is_set
    )

    assert result["cancelled"] is True
    assert time.monotonic() - started < 5


def test_worker_client_timeout_reports_the_last_redacted_diagnostic_stage(tmp_path, monkeypatch):
    import native_worker_client
    from native_worker_client import NativeWorkerClient, WorkerError

    diagnostic = tmp_path / "worker.jsonl"
    monkeypatch.setattr(native_worker_client, "_worker_diagnostic_path", lambda _request_id: diagnostic)
    command = _worker_script(
        tmp_path,
        """import json, os, sys, time
json.loads(sys.stdin.readline())
with open(os.environ["MULTISOCIAL_WORKER_DIAGNOSTIC_PATH"], "w", encoding="utf-8") as output:
    output.write('{"stage":"preload:mediapipe"}\\n')
time.sleep(30)
""",
    )

    with pytest.raises(WorkerError, match="preload:mediapipe"):
        NativeWorkerClient(command=command, timeout_seconds=0.05).run("probe", {})

    assert diagnostic.is_file()

def test_forced_cancellation_cleans_only_its_request_staging(tmp_path):
    from native_worker_client import NativeWorkerClient

    destination = tmp_path / "outputs"
    destination.mkdir()
    unrelated = destination / ".multisocial-worker-unrelated-keep"
    unrelated.mkdir()
    command = _worker_script(
        tmp_path,
        """import json, pathlib, sys, time
request = json.loads(sys.stdin.readline())
destination = pathlib.Path(request["payload"]["output_audio_features_folder"])
(destination / (".multisocial-worker-" + request["id"] + "-orphan")).mkdir()
time.sleep(30)
""",
    )
    result = NativeWorkerClient(
        command=command,
        cancel_grace_seconds=0.05,
    ).run(
        "extract_audio_features",
        {"output_audio_features_folder": str(destination)},
        cancel_check=lambda: True,
    )

    assert result["cancelled"] is True
    assert unrelated.is_dir()
    assert list(destination.iterdir()) == [unrelated]


def test_windows_worker_launch_does_not_show_a_console(monkeypatch):
    import native_worker_client

    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(native_worker_client.sys, "platform", "win32")
    monkeypatch.setattr(native_worker_client.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(native_worker_client.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(native_worker_client.subprocess, "SW_HIDE", 0, raising=False)

    kwargs = native_worker_client._worker_popen_kwargs()

    assert set(kwargs) == {"startupinfo"}
    assert kwargs["startupinfo"].dwFlags == 1
    assert kwargs["startupinfo"].wShowWindow == 0


def test_frozen_windows_worker_uses_its_private_runtime_directory(tmp_path, monkeypatch):
    import native_worker_client

    executable = tmp_path / "MultiSOCIAL-Standard.exe"
    worker = tmp_path / "worker" / "MultiSOCIAL-Worker-Launcher.exe"
    worker.parent.mkdir()
    executable.touch()
    worker.touch()
    monkeypatch.setattr(native_worker_client.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native_worker_client.sys, "executable", str(executable))

    assert native_worker_client.worker_command() == [str(worker)]


def test_packaged_windows_worker_environment_removes_gui_state(tmp_path, monkeypatch):
    import native_worker_client

    gui = tmp_path / "app" / "MultiSOCIAL-Standard.exe"
    worker = gui.parent / "worker" / "MultiSOCIAL-Worker-Launcher.exe"
    worker.parent.mkdir(parents=True)
    gui.touch()
    worker.touch()
    monkeypatch.setattr(native_worker_client.sys, "platform", "win32")
    monkeypatch.setattr(native_worker_client.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native_worker_client.sys, "executable", str(gui))
    monkeypatch.setenv("PATH", f"{gui.parent};C:\\Windows\\System32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("PYTHONHOME", "bad")
    monkeypatch.setenv("PYTHONPATH", "bad")
    monkeypatch.setenv("MULTISOCIAL_FFMPEG_EXE", str(gui.parent / "ffmpeg.exe"))

    env = native_worker_client._packaged_windows_environment([str(worker)], None)

    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["PATH"].split(";")[0] == str(worker.parent)
    assert str(gui.parent) not in env["PATH"].split(";")[1:]
    windows_root = native_worker_client.Path(r"C:\Windows")
    assert env["PATH"].split(";")[1:] == [
        str(windows_root / "System32"),
        str(windows_root),
        str(windows_root / "System32" / "Wbem"),
        str(windows_root / "System32" / "WindowsPowerShell" / "v1.0"),
    ]
    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "MULTISOCIAL_FFMPEG_EXE" not in env

def test_worker_payload_paths_are_absolute_before_worker_cwd_changes(tmp_path, monkeypatch):
    import native_worker_client

    monkeypatch.chdir(tmp_path)
    payload = native_worker_client._absolutize_worker_payload(
        {
            "video_files": ["inputs/video.avi"],
            "output_csv_folder": "outputs",
            "alignment_pairs": [["features.csv", "transcript.json", "aligned.csv"]],
        }
    )

    assert payload["video_files"] == [str(tmp_path / "inputs" / "video.avi")]
    assert payload["output_csv_folder"] == str(tmp_path / "outputs")
    assert payload["alignment_pairs"] == [[
        str(tmp_path / "features.csv"),
        str(tmp_path / "transcript.json"),
        str(tmp_path / "aligned.csv"),
    ]]


def test_packaged_windows_spawn_does_not_mutate_gui_dll_directory(monkeypatch):
    import native_worker_client

    sentinel = object()
    monkeypatch.setattr(native_worker_client.subprocess, "Popen", lambda *args, **kwargs: sentinel)

    result = native_worker_client._spawn_worker(["MultiSOCIAL-Worker.exe"])

    assert result is sentinel


def test_worker_client_preserves_a_caller_supplied_diagnostic_path(tmp_path, monkeypatch):
    import native_worker_client
    from native_worker_client import NativeWorkerClient

    diagnostic = tmp_path / "caller-diagnostic.jsonl"
    command = _worker_script(
        tmp_path,
        """import json, os, sys
request = json.loads(sys.stdin.readline())
with open(os.environ["MULTISOCIAL_WORKER_DIAGNOSTIC_PATH"], "w", encoding="utf-8") as output:
    output.write('{"stage":"result-emitted"}\\n')
print(json.dumps({"protocol": 1, "id": request["id"], "event": "result", "result": {}}), flush=True)
""",
    )
    monkeypatch.setenv(native_worker_client.WORKER_DIAGNOSTIC_ENV, str(diagnostic))

    NativeWorkerClient(command=command).run("probe", {})

    assert not diagnostic.exists()


def test_worker_probe_metadata_reports_the_current_architecture():
    from analysis_worker import _runtime_metadata

    metadata = _runtime_metadata()

    assert metadata["platform"] == sys.platform
    assert metadata["is_64bit"] is (sys.maxsize > 2**32)
    assert "worker_runtime" in metadata


def test_worker_diagnostics_exclude_unapproved_detail_fields(tmp_path, monkeypatch):
    import json
    import analysis_worker

    diagnostic = tmp_path / "worker.jsonl"
    monkeypatch.setenv(analysis_worker.WORKER_DIAGNOSTIC_ENV, str(diagnostic))

    analysis_worker._diagnostic_stage(
        "preload:mediapipe",
        operation="probe",
        secret="hf-private-token",
        path=str(tmp_path),
    )

    record = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert record["stage"] == "preload:mediapipe"
    assert record["operation"] == "probe"
    assert "secret" not in record
    assert "path" not in record


def test_worker_runtime_flags_dlls_loaded_from_gui_root(tmp_path, monkeypatch):
    import analysis_worker

    worker_root = tmp_path / "app" / "worker"
    worker_root.mkdir(parents=True)
    monkeypatch.setattr(analysis_worker.sys, "platform", "win32")
    monkeypatch.setattr(analysis_worker.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        analysis_worker,
        "_loaded_windows_module_paths",
        lambda: [
            worker_root / "torch_cpu.dll",
            worker_root.parent / "wxbase.dll",
        ],
    )

    assert analysis_worker._loaded_module_violations(worker_root) == ["wxbase.dll"]


def test_worker_runtime_flags_package_native_modules_outside_worker(tmp_path, monkeypatch):
    import analysis_worker

    worker_root = tmp_path / "app" / "worker"
    worker_root.mkdir(parents=True)
    monkeypatch.setattr(analysis_worker.sys, "platform", "win32")
    monkeypatch.setattr(analysis_worker.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        analysis_worker,
        "_loaded_windows_module_paths",
        lambda: [
            worker_root / "torch_cpu.dll",
            tmp_path / "foreign" / "_framework_bindings.pyd",
        ],
    )

    assert analysis_worker._native_module_violations(worker_root) == ["_framework_bindings.pyd"]

def test_worker_runtime_rejects_all_non_system_external_modules(tmp_path, monkeypatch):
    import analysis_worker

    worker_root = tmp_path / "app" / "worker"
    windows_root = tmp_path / "Windows"
    worker_root.mkdir(parents=True)
    (windows_root / "System32").mkdir(parents=True)
    monkeypatch.setattr(analysis_worker.sys, "platform", "win32")
    monkeypatch.setattr(analysis_worker.sys, "frozen", True, raising=False)
    monkeypatch.setenv("SystemRoot", str(windows_root))
    loaded = [
        worker_root / "MultiSOCIAL-Worker.exe",
        windows_root / "System32" / "kernel32.dll",
        tmp_path / "foreign" / "injected.dll",
    ]

    assert analysis_worker._external_module_violations(worker_root, loaded) == ["injected.dll"]


def test_worker_preloads_pose_runtime_in_mediapipe_then_torch_order(monkeypatch):
    import analysis_worker

    events = []
    monkeypatch.setattr(analysis_worker, "_import_worker_runtime_module", lambda name: events.append(name))
    monkeypatch.setattr(analysis_worker, "_enable_worker_tensor_loader", lambda: events.append("enable-torch"))

    analysis_worker._initialize_worker_operation_runtime("extract_pose", {})

    assert events == ["mediapipe", "cv2", "enable-torch", "pose"]


def test_worker_preloads_complete_probe_runtime_on_the_main_thread(monkeypatch):
    import analysis_worker

    events = []
    monkeypatch.setattr(analysis_worker, "_import_worker_runtime_module", lambda name: events.append(name))
    monkeypatch.setattr(analysis_worker, "_enable_worker_tensor_loader", lambda: events.append("enable-torch"))

    analysis_worker._initialize_worker_operation_runtime("probe", {"profile": "complete"})

    assert events == ["mediapipe", "cv2", "opensmile", "enable-torch", "torch", "torchaudio", "pyannote.audio"]

def test_worker_preloads_diarization_dependencies_before_provenance_check(monkeypatch):
    import analysis_worker

    events = []
    monkeypatch.setattr(
        analysis_worker,
        "_import_worker_runtime_module",
        lambda name: events.append(name),
    )
    monkeypatch.setattr(
        analysis_worker,
        "_enable_worker_tensor_loader",
        lambda: events.append("enable-torch"),
    )

    analysis_worker._initialize_worker_operation_runtime(
        "extract_transcripts",
        {"enable_diarization": True},
    )

    assert events == ["enable-torch", "audio", "torchaudio", "pyannote.audio"]
    assert "pyannote.audio" in analysis_worker._operation_module_names(
        "extract_transcripts",
        {"enable_diarization": True},
    )


def test_staged_output_promotes_only_complete_files(tmp_path):
    from analysis_worker import _StagedOutput

    destination = tmp_path / "destination"
    stage = _StagedOutput(str(destination))
    with stage as staged:
        staged_file = os.path.join(staged, "result.csv")
        with open(staged_file, "w", encoding="utf-8") as handle:
            handle.write("complete")
        stage.commit()

    assert (destination / "result.csv").read_text(encoding="utf-8") == "complete"
    assert not list(destination.glob(".multisocial-worker-*"))


def test_staged_output_removes_cancelled_partial_files(tmp_path):
    from analysis_worker import _StagedOutput

    destination = tmp_path / "destination"
    with _StagedOutput(str(destination)) as staged:
        with open(os.path.join(staged, "partial.csv"), "w", encoding="utf-8") as handle:
            handle.write("partial")

    assert not (destination / "partial.csv").exists()
    assert not list(destination.glob(".multisocial-worker-*"))

def test_staged_output_names_are_scoped_to_request_id(tmp_path):
    from analysis_worker import _StagedOutput

    destination = tmp_path / "destination"
    with _StagedOutput(str(destination), "request-123") as staged:
        assert os.path.basename(staged).startswith(
            ".multisocial-worker-request-123-"
        )

def test_packaged_smoke_request_writes_redacted_structured_failure(tmp_path, monkeypatch):
    import worker_backend

    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        '{"operation":"probe","payload":{"profile":"complete"}}',
        encoding="utf-8",
    )
    token = "hf-private-token"

    class FailingClient:
        def run(self, *_args, **_kwargs):
            raise RuntimeError(f"model rejected {token}")

    monkeypatch.setattr(worker_backend, "NativeWorkerClient", FailingClient)
    monkeypatch.setenv(worker_backend.SMOKE_REQUEST_ENV, str(request))
    monkeypatch.setenv(worker_backend.SMOKE_RESULT_ENV, str(result))
    monkeypatch.setenv(worker_backend.WORKER_TOKEN_ENV, token)

    with pytest.raises(RuntimeError, match="REDACTED") as exc:
        worker_backend._run_packaged_smoke_request()
    assert token not in str(exc.value)

    response = __import__("json").loads(result.read_text(encoding="utf-8"))
    assert response == {
        "ok": False,
        "error": "model rejected [REDACTED]",
    }
    assert worker_backend.WORKER_TOKEN_ENV not in os.environ
