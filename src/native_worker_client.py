"""Windows-only client and lightweight facades for the private analysis worker.

This module intentionally has no native ML imports.  It is safe for the GUI
process to import before wxPython starts on every platform.
"""

from __future__ import annotations

import glob
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional


PROTOCOL_VERSION = 1
WORKER_TOKEN_ENV = "MULTISOCIAL_WORKER_HF_TOKEN"
_WINDOWS_LAUNCH_LOCK = threading.Lock()


class WorkerError(RuntimeError):
    """A worker failed without returning a successful operation result."""


def is_windows_worker_enabled() -> bool:
    """Return whether this launch must isolate native analysis from the GUI."""
    return sys.platform == "win32"


def find_pose_csv_paths(output_csv_folder: str, video_path: str, multi_person: Optional[bool] = None) -> list[str]:
    """Native-import-free equivalent of ``pose.find_pose_csv_paths``."""
    base = os.path.splitext(os.path.basename(video_path))[0]
    if multi_person is True:
        pattern = f"{base}_multi_ID_*.csv"
    elif multi_person is False:
        pattern = f"{base}_ID_*.csv"
    else:
        pattern = f"{base}*_ID_*.csv"
    return sorted(glob.glob(os.path.join(output_csv_folder, pattern)))


def _redact(value: str, token: Optional[str]) -> str:
    text = str(value or "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text.replace("\r", " ").replace("\n", " ")[-2000:]


def worker_command() -> list[str]:
    """Locate the bundled console worker, or run it from source for tests."""
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent / "worker" / "MultiSOCIAL-Worker.exe"
        if not candidate.is_file():
            raise WorkerError(f"Bundled analysis worker is missing: {candidate.name}")
        return [str(candidate)]
    return [sys.executable, str(Path(__file__).with_name("analysis_worker.py"))]


def _worker_popen_kwargs() -> dict[str, Any]:
    """Keep the private console worker invisible when launched by the GUI."""
    if sys.platform != "win32":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


def _path_is_within(path: str | Path, root: str | Path) -> bool:
    try:
        Path(os.path.normcase(os.path.abspath(path))).relative_to(
            Path(os.path.normcase(os.path.abspath(root)))
        )
        return True
    except (OSError, ValueError):
        return False


def _worker_directory(command: list[str]) -> Path:
    executable = Path(command[0]).resolve()
    if executable.name.casefold() == "multiSOCIAL-worker.exe".casefold():
        return executable.parent
    if len(command) > 1:
        return Path(command[1]).resolve().parent
    return executable.parent


def _packaged_windows_environment(command: list[str], token: Optional[str]) -> dict[str, str]:
    env = os.environ.copy()
    if token:
        env[WORKER_TOKEN_ENV] = token
    else:
        env.pop(WORKER_TOKEN_ENV, None)

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return env

    app_root = Path(sys.executable).resolve().parent
    worker_dir = _worker_directory(command)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)

    path_separator = ";"
    inherited_path = env.get("PATH", "")
    clean_entries = [
        entry
        for entry in inherited_path.split(path_separator)
        if entry and not _path_is_within(entry, app_root)
    ]
    env["PATH"] = path_separator.join([str(worker_dir), *clean_entries])

    inherited_ffmpeg = env.get("MULTISOCIAL_FFMPEG_EXE")
    if inherited_ffmpeg and _path_is_within(inherited_ffmpeg, app_root):
        env.pop("MULTISOCIAL_FFMPEG_EXE", None)
    return env


def _spawn_worker(command: list[str], **kwargs: Any) -> subprocess.Popen:
    """Spawn a packaged worker without inheriting the GUI DLL search directory."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.Popen(command, **kwargs)

    gui_bundle_root = str(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve())
    kernel32 = ctypes.windll.kernel32
    with _WINDOWS_LAUNCH_LOCK:
        if not kernel32.SetDllDirectoryW(None):
            raise WorkerError("Could not clear the GUI DLL search directory before starting the worker")
        process = None
        try:
            process = subprocess.Popen(command, **kwargs)
        finally:
            if not kernel32.SetDllDirectoryW(gui_bundle_root):
                if process is not None:
                    process.terminate()
                raise WorkerError("Could not restore the GUI DLL search directory after starting the worker")
        return process


class NativeWorkerClient:
    """Run exactly one native operation in an isolated console child process."""

    def __init__(self, *, command: Optional[list[str]] = None, cancel_grace_seconds: float = 15.0):
        self.command = command or worker_command()
        self.cancel_grace_seconds = cancel_grace_seconds

    def run(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        progress_callback: Optional[Callable[[int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        hf_token: Optional[str] = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        env = _packaged_windows_environment(self.command, hf_token)
        worker_dir = _worker_directory(self.command)

        process = _spawn_worker(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=str(worker_dir),
            **_worker_popen_kwargs(),
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None

        events: queue.Queue[tuple[str, str]] = queue.Queue()
        stderr_lines: list[str] = []

        def read_stdout() -> None:
            for line in process.stdout:
                events.put(("stdout", line))
            events.put(("stdout_eof", ""))

        def read_stderr() -> None:
            for line in process.stderr:
                cleaned = _redact(line, hf_token)
                if cleaned:
                    stderr_lines.append(cleaned)
                    del stderr_lines[:-20]

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()

        request = {
            "protocol": PROTOCOL_VERSION,
            "id": request_id,
            "type": "run",
            "operation": operation,
            "payload": payload,
        }
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()

        cancel_sent = False
        cancel_deadline: Optional[float] = None
        try:
            while True:
                if cancel_check and cancel_check() and not cancel_sent:
                    cancel_sent = True
                    cancel_deadline = time.monotonic() + self.cancel_grace_seconds
                    process.stdin.write(json.dumps({"protocol": PROTOCOL_VERSION, "id": request_id, "type": "cancel"}) + "\n")
                    process.stdin.flush()

                if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return {"succeeded": [], "failed": [], "cancelled": True}

                try:
                    event_kind, line = events.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None:
                        detail = " | ".join(stderr_lines[-5:]) or f"worker exited with code {process.returncode}"
                        raise WorkerError(_redact(detail, hf_token))
                    continue

                if event_kind != "stdout":
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    stderr_lines.append(_redact(line, hf_token))
                    continue
                if message.get("protocol") != PROTOCOL_VERSION or message.get("id") != request_id:
                    continue
                event = message.get("event")
                if event == "progress" and progress_callback:
                    progress_callback(int(message.get("value", 0)))
                elif event == "status" and status_callback:
                    status_callback(str(message.get("message", "")))
                elif event == "result":
                    result = dict(message.get("result") or {})
                    result.setdefault("succeeded", [])
                    result.setdefault("failed", [])
                    result.setdefault("cancelled", False)
                    return result
                elif event == "error":
                    raise WorkerError(_redact(str(message.get("message", "Worker operation failed")), hf_token))
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()


class WindowsPoseProcessor:
    """PoseProcessor-compatible Windows façade backed by ``MultiSOCIAL-Worker``."""

    def __init__(self, output_csv_folder, output_video_folder=None, status_callback=None, frame_threshold=10, frame_stride=1, downscale_to=None):
        self.output_csv_folder = output_csv_folder
        self.output_video_folder = output_video_folder
        self.status_callback = status_callback
        self.frame_threshold = frame_threshold
        self.frame_stride = frame_stride
        self.downscale_to = downscale_to
        self.enable_multi_person_pose = False
        self._client = NativeWorkerClient()

    def set_multi_person_mode(self, enabled: bool) -> None:
        self.enable_multi_person_pose = bool(enabled)

    def _payload(self, video_path: str) -> dict[str, Any]:
        return {
            "video_files": [video_path],
            "output_csv_folder": self.output_csv_folder,
            "output_video_folder": self.output_video_folder,
            "frame_threshold": self.frame_threshold,
            "frame_stride": self.frame_stride,
            "downscale_to": self.downscale_to,
            "multi_person": self.enable_multi_person_pose,
        }

    def extract_pose_features(self, video_path, progress_callback=None, cancel_check=None):
        result = self._client.run("extract_pose", self._payload(video_path), progress_callback=progress_callback, status_callback=self.status_callback, cancel_check=cancel_check)
        if result["cancelled"]:
            return False
        if result["failed"]:
            raise WorkerError(result["failed"][0][1])
        return True

    def embed_pose_video(self, video_path, progress_callback=None, cancel_check=None):
        result = self._client.run("embed_pose", self._payload(video_path), progress_callback=progress_callback, status_callback=self.status_callback, cancel_check=cancel_check)
        if result["cancelled"]:
            return False
        if result["failed"]:
            raise WorkerError(result["failed"][0][1])
        return result["succeeded"][0] if result["succeeded"] else None


class WindowsAudioProcessor:
    """AudioProcessor-compatible Windows façade backed by the private worker."""

    def __init__(self, output_audio_features_folder, output_transcripts_folder, status_callback=None, enable_speaker_diarization=False, auth_token=None):
        self.output_audio_features_folder = output_audio_features_folder
        self.output_transcripts_folder = output_transcripts_folder
        self.status_callback = status_callback
        self.enable_speaker_diarization = enable_speaker_diarization
        self.auth_token = auth_token
        self._client = NativeWorkerClient()

    def _run(self, operation, payload, progress_callback=None, cancel_check=None):
        return self._client.run(operation, payload, progress_callback=progress_callback, status_callback=self.status_callback, cancel_check=cancel_check, hf_token=self.auth_token if self.enable_speaker_diarization else None)

    def preload_speaker_diarizer(self):
        if not self.enable_speaker_diarization:
            return
        self._run("probe", {"profile": "complete", "validate_diarization": True})

    def extract_audio_features_batch(self, audio_files, progress_callback=None, cancel_check=None):
        return self._run("extract_audio_features", {"audio_files": list(audio_files), "output_audio_features_folder": self.output_audio_features_folder}, progress_callback, cancel_check)

    def extract_audio_features(self, audio_file, progress_callback=None, cancel_check=None):
        result = self.extract_audio_features_batch([audio_file], progress_callback, cancel_check)
        if result["cancelled"]:
            return False
        if result["failed"]:
            raise WorkerError(result["failed"][0][1])
        return result["succeeded"][0]

    def extract_transcripts_batch(self, audio_files, progress_callback=None, word_timestamps=False, cancel_check=None):
        return self._run("extract_transcripts", {"audio_files": list(audio_files), "output_transcripts_folder": self.output_transcripts_folder, "word_timestamps": bool(word_timestamps), "enable_diarization": bool(self.enable_speaker_diarization)}, progress_callback, cancel_check)

    def extract_transcript(self, audio_file, word_timestamps=False, progress_callback=None, cancel_check=None):
        result = self.extract_transcripts_batch([audio_file], progress_callback, word_timestamps, cancel_check)
        if result["cancelled"]:
            return False
        if result["failed"]:
            raise WorkerError(result["failed"][0][1])
        return result["succeeded"][0]

    def align_features_batch(self, alignment_pairs, progress_callback=None):
        return self._run("align_features", {"alignment_pairs": [list(pair) for pair in alignment_pairs]}, progress_callback)
