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


def test_windows_worker_launch_does_not_show_a_console(monkeypatch):
    import native_worker_client

    monkeypatch.setattr(native_worker_client.sys, "platform", "win32")
    monkeypatch.setattr(native_worker_client.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    assert native_worker_client._worker_popen_kwargs() == {"creationflags": 0x08000000}


def test_worker_probe_metadata_reports_the_current_architecture():
    from analysis_worker import _runtime_metadata

    metadata = _runtime_metadata()

    assert metadata["platform"] == sys.platform
    assert metadata["is_64bit"] is (sys.maxsize > 2**32)


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
