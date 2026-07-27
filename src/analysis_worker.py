"""Private console worker for Windows native analysis.

The worker imports native ML libraries only after the GUI has spawned it.  Its
stdout is reserved for the versioned JSON-lines protocol; all library output is
redirected to redacted stderr diagnostics.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import importlib
import io
import json
import os
import platform
import queue
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from native_worker_client import PROTOCOL_VERSION, WORKER_TOKEN_ENV


class _RedactingStream(io.TextIOBase):
    def __init__(self, target, token: str | None):
        self.target = target
        self.token = token

    def write(self, value):
        text = str(value)
        if self.token:
            text = text.replace(self.token, "[REDACTED]")
        return self.target.write(text)

    def flush(self):
        return self.target.flush()


def _emit(request_id: str, event: str, **values: Any) -> None:
    message = {"protocol": PROTOCOL_VERSION, "id": request_id, "event": event, **values}
    print(json.dumps(message, separators=(",", ":")), flush=True)


def _safe_error(error: BaseException, token: str | None) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text[-2000:] or type(error).__name__


class _StagedOutput:
    """Stage generated files beside their destination and promote atomically."""

    def __init__(self, destination: str | None):
        self.destination = Path(destination) if destination else None
        self.path: Path | None = None

    def __enter__(self) -> str | None:
        if self.destination is None:
            return None
        self.destination.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=".multisocial-worker-", dir=self.destination))
        return str(self.path)

    def commit(self) -> None:
        if self.destination is None or self.path is None:
            return
        for source in sorted(self.path.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(self.path)
            target = self.destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path is not None:
            shutil.rmtree(self.path, ignore_errors=True)


def _batch_outcome() -> dict[str, Any]:
    return {"succeeded": [], "failed": [], "cancelled": False}


def _runtime_metadata() -> dict[str, Any]:
    """Return non-sensitive host facts used by packaged Windows compatibility checks."""
    machine = platform.machine().lower()
    is_64bit = sys.maxsize > 2**32
    metadata: dict[str, Any] = {
        "platform": sys.platform,
        "machine": machine,
        "is_64bit": is_64bit,
        "architecture": "x64" if is_64bit and machine in {"amd64", "x86_64"} else machine,
    }
    if sys.platform == "win32":
        release, version, _, _ = platform.win32_ver()
        metadata["windows_release"] = release
        metadata["windows_version"] = version
        try:
            metadata["windows_major"] = int(version.split(".", 1)[0])
        except (TypeError, ValueError):
            metadata["windows_major"] = None
    metadata["worker_runtime"] = _worker_runtime_diagnostics()
    return metadata


def _worker_runtime_diagnostics() -> dict[str, Any]:
    """Return safe evidence that native imports came from the private worker."""
    executable_dir = Path(sys.executable).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", executable_dir)).resolve()
    binding_hashes = []
    if bundle_root.is_dir():
        for binding in sorted(bundle_root.rglob("_framework_bindings*.pyd")):
            try:
                digest = hashlib.sha256(binding.read_bytes()).hexdigest()
            except OSError:
                continue
            binding_hashes.append({"name": binding.name, "sha256": digest})
    return {
        "private_runtime": executable_dir.name.casefold() == "worker" and bundle_root == executable_dir,
        "mediapipe_bindings": binding_hashes,
        "loaded_module_violations": _loaded_module_violations(bundle_root),
        "native_module_violations": _native_module_violations(bundle_root),
    }


def _path_is_within(path: str | Path, root: str | Path) -> bool:
    try:
        Path(os.path.normcase(os.path.abspath(path))).relative_to(
            Path(os.path.normcase(os.path.abspath(root)))
        )
        return True
    except (OSError, ValueError):
        return False


def _loaded_windows_module_paths() -> list[Path]:
    """Enumerate this process' loaded modules without adding a native dependency."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    process = ctypes.windll.kernel32.GetCurrentProcess()
    module_array = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    enum_modules = getattr(ctypes.windll.kernel32, "K32EnumProcessModules", None)
    module_filename = getattr(ctypes.windll.kernel32, "K32GetModuleFileNameExW", None)
    if enum_modules is None or module_filename is None:
        enum_modules = ctypes.windll.psapi.EnumProcessModules
        module_filename = ctypes.windll.psapi.GetModuleFileNameExW
    if not enum_modules(process, module_array, ctypes.sizeof(module_array), ctypes.byref(needed)):
        raise OSError("Could not enumerate loaded worker modules")

    count = min(len(module_array), needed.value // ctypes.sizeof(wintypes.HMODULE))
    paths: list[Path] = []
    for module in module_array[:count]:
        buffer = ctypes.create_unicode_buffer(32768)
        if module_filename(process, module, buffer, len(buffer)):
            paths.append(Path(buffer.value).resolve())
    return paths


def _loaded_module_violations(bundle_root: Path) -> list[str]:
    """Return DLLs loaded from the containing GUI runtime instead of worker/."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return []
    app_root = bundle_root.parent
    violations = []
    for path in _loaded_windows_module_paths():
        if _path_is_within(path, app_root) and not _path_is_within(path, bundle_root):
            violations.append(path.name)
    return sorted(set(violations), key=str.casefold)


def _native_module_violations(bundle_root: Path) -> list[str]:
    """Return package-native extensions or DLLs resolved outside worker/."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return []
    native_markers = (
        "_framework_bindings",
        "audresample",
        "c10",
        "fbgemm",
        "libiomp",
        "mediapipe",
        "opencv",
        "smileapi",
        "torch",
    )
    violations = []
    for path in _loaded_windows_module_paths():
        basename = path.name.casefold()
        package_native = path.suffix.casefold() == ".pyd" or any(
            marker in basename for marker in native_markers
        )
        if package_native and not _path_is_within(path, bundle_root):
            violations.append(path.name)
    return sorted(set(violations), key=str.casefold)


def _operation_module_names(operation: str, payload: dict[str, Any]) -> tuple[str, ...]:
    if operation in {"extract_pose", "embed_pose"}:
        return ("mediapipe", "mediapipe.python._framework_bindings", "cv2", "torch", "pose")
    if operation in {"extract_audio_features", "extract_transcripts", "align_features"}:
        return ("audio", "opensmile", "torch", "transformers")
    if operation == "probe":
        names = ["mediapipe", "mediapipe.python._framework_bindings", "cv2", "opensmile", "torch"]
        if payload.get("profile") == "complete":
            names.extend(["torchaudio", "pyannote.audio"])
        return tuple(names)
    return ()


def _validate_worker_runtime_provenance(operation: str, payload: dict[str, Any]) -> None:
    """Fail closed if a frozen worker resolved native state outside worker/."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    executable_dir = Path(sys.executable).resolve().parent
    bundle_root = Path(getattr(sys, "_MEIPASS", executable_dir)).resolve()
    if executable_dir.name.casefold() != "worker" or bundle_root != executable_dir:
        raise RuntimeError("Worker executable and bundle root are not the private worker runtime")

    python_dll = bundle_root / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    if not python_dll.is_file():
        raise RuntimeError(f"Private worker Python runtime is missing: {python_dll.name}")

    for module_name in _operation_module_names(operation, payload):
        module = sys.modules.get(module_name)
        module_path = getattr(module, "__file__", None)
        if not module_path or not _path_is_within(module_path, bundle_root):
            raise RuntimeError(f"Worker module resolved outside private runtime: {module_name}")

    loaded_paths = _loaded_windows_module_paths()
    loaded_python = [path for path in loaded_paths if path.name.casefold() == python_dll.name.casefold()]
    if not loaded_python or any(not _path_is_within(path, bundle_root) for path in loaded_python):
        raise RuntimeError(f"Loaded Python runtime did not resolve beneath worker/: {python_dll.name}")

    violations = _loaded_module_violations(bundle_root)
    if violations:
        raise RuntimeError(
            "Worker loaded DLLs from the GUI runtime: " + ", ".join(violations[:10])
        )
    native_violations = _native_module_violations(bundle_root)
    if native_violations:
        raise RuntimeError(
            "Worker loaded package-native modules outside worker/: "
            + ", ".join(native_violations[:10])
        )


def _import_worker_runtime_module(module_name: str) -> Any:
    """Import through a replaceable seam so preload order is unit-testable."""
    return importlib.import_module(module_name)


def _initialize_worker_operation_runtime(operation: str, payload: dict[str, Any]) -> None:
    """Initialize each native extension on the worker main thread.

    The loader phases deliberately preserve MediaPipe-before-Torch ordering.
    Operation handlers subsequently import only already-cached modules from
    their background thread.
    """
    if operation in {"extract_pose", "embed_pose"}:
        _import_worker_runtime_module("mediapipe")
        _import_worker_runtime_module("cv2")
        _enable_worker_tensor_loader()
        _import_worker_runtime_module("pose")
        return
    if operation in {"extract_audio_features", "extract_transcripts", "align_features"}:
        _enable_worker_tensor_loader()
        _import_worker_runtime_module("audio")
        return
    if operation == "probe":
        _import_worker_runtime_module("mediapipe")
        _import_worker_runtime_module("cv2")
        _import_worker_runtime_module("opensmile")
        _enable_worker_tensor_loader()
        _import_worker_runtime_module("torch")
        if payload.get("profile") == "complete":
            _import_worker_runtime_module("torchaudio")
            _import_worker_runtime_module("pyannote.audio")


def _configure_worker_ffmpeg() -> None:
    """Use the worker's ffmpeg copy instead of an inherited GUI executable."""
    if not getattr(sys, "frozen", False):
        return
    import imageio_ffmpeg

    executable = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    if not _path_is_within(executable, bundle_root):
        raise RuntimeError("Worker ffmpeg resolved outside the private worker runtime")
    os.environ["MULTISOCIAL_FFMPEG_EXE"] = str(executable)


def _run_pose(payload: dict[str, Any], cancelled: threading.Event, emit_progress, emit_status, *, embed: bool) -> dict[str, Any]:
    from pose import PoseProcessor, find_pose_csv_paths

    videos = list(payload.get("video_files") or [])
    output_key = "output_video_folder" if embed else "output_csv_folder"
    destination = payload.get(output_key)
    outcome = _batch_outcome()
    if not destination:
        raise ValueError(f"{output_key} is required")

    stage_context = _StagedOutput(destination)
    with stage_context as staged:
        processor = PoseProcessor(
            output_csv_folder=payload.get("output_csv_folder") if embed else staged,
            output_video_folder=staged if embed else None,
            status_callback=emit_status,
            frame_threshold=payload.get("frame_threshold", 10),
            frame_stride=payload.get("frame_stride", 1),
            downscale_to=tuple(payload["downscale_to"]) if payload.get("downscale_to") else None,
        )
        processor.set_multi_person_mode(bool(payload.get("multi_person")))
        total = max(1, len(videos))
        for index, video in enumerate(videos, start=1):
            if cancelled.is_set():
                outcome["cancelled"] = True
                break
            start = int((index - 1) * 100 / total)
            span = 100 / total
            try:
                operation = processor.embed_pose_video if embed else processor.extract_pose_features
                result = operation(
                    video,
                    progress_callback=lambda value, start=start, span=span: emit_progress(int(start + value * span / 100)),
                    cancel_check=cancelled.is_set,
                )
                if result is False:
                    outcome["cancelled"] = True
                    break
                if embed:
                    if result is None:
                        raise RuntimeError("no pose CSV found")
                    outcome["succeeded"].append(os.path.join(destination, os.path.basename(result)))
                else:
                    paths = find_pose_csv_paths(staged, video, multi_person=bool(payload.get("multi_person")))
                    if not paths:
                        raise RuntimeError("no pose CSV was produced")
                    outcome["succeeded"].append(video)
            except Exception as exc:
                outcome["failed"].append((video, str(exc)))
        if not outcome["cancelled"]:
            stage_context.commit()
    emit_progress(100)
    return outcome


def _run_audio_features(payload: dict[str, Any], cancelled: threading.Event, emit_progress, emit_status) -> dict[str, Any]:
    _enable_worker_tensor_loader()
    from audio import AudioProcessor

    destination = payload.get("output_audio_features_folder")
    if not destination:
        raise ValueError("output_audio_features_folder is required")
    stage_context = _StagedOutput(destination)
    with stage_context as stage:
        processor = AudioProcessor(stage, None, status_callback=emit_status)
        outcome = processor.extract_audio_features_batch(payload.get("audio_files") or [], progress_callback=emit_progress, cancel_check=cancelled.is_set)
        if not outcome["cancelled"]:
            for index, path in enumerate(outcome["succeeded"]):
                outcome["succeeded"][index] = os.path.join(destination, os.path.basename(path))
            stage_context.commit()
        return outcome


def _run_transcripts(payload: dict[str, Any], cancelled: threading.Event, emit_progress, emit_status, token: str | None) -> dict[str, Any]:
    _enable_worker_tensor_loader()
    from audio import AudioProcessor

    destination = payload.get("output_transcripts_folder")
    if not destination:
        raise ValueError("output_transcripts_folder is required")
    diarization = bool(payload.get("enable_diarization"))
    stage_context = _StagedOutput(destination)
    with stage_context as stage:
        processor = AudioProcessor(None, stage, status_callback=emit_status, enable_speaker_diarization=diarization, auth_token=token if diarization else None)
        outcome = processor.extract_transcripts_batch(payload.get("audio_files") or [], progress_callback=emit_progress, word_timestamps=bool(payload.get("word_timestamps")), cancel_check=cancelled.is_set)
        if not outcome["cancelled"]:
            for index, path in enumerate(outcome["succeeded"]):
                outcome["succeeded"][index] = os.path.join(destination, os.path.basename(path))
            stage_context.commit()
        return outcome


def _run_alignment(payload: dict[str, Any], cancelled: threading.Event, emit_progress, emit_status) -> dict[str, Any]:
    _enable_worker_tensor_loader()
    from audio import AudioProcessor

    pairs = [tuple(item) for item in payload.get("alignment_pairs") or []]
    outcome = _batch_outcome()
    total = max(1, len(pairs))
    for index, (features_csv, transcript_json, output_csv) in enumerate(pairs, start=1):
        if cancelled.is_set():
            outcome["cancelled"] = True
            break
        destination = str(Path(output_csv).parent)
        stage_context = _StagedOutput(destination)
        with stage_context as stage:
            staged_output = str(Path(stage) / Path(output_csv).name)
            try:
                processor = AudioProcessor(None, None, status_callback=emit_status)
                result = processor.align_features(features_csv, transcript_json, staged_output)
                if result != staged_output or not os.path.isfile(staged_output):
                    raise RuntimeError("Alignment did not write its output CSV")
                stage_context.commit()
                outcome["succeeded"].append(output_csv)
            except Exception as exc:
                outcome["failed"].append((output_csv, str(exc)))
        emit_progress(int(index * 100 / total))
    return outcome


def _run_probe(payload: dict[str, Any], token: str | None) -> dict[str, Any]:
    import mediapipe  # noqa: F401
    import cv2  # noqa: F401
    import opensmile  # noqa: F401

    _enable_worker_tensor_loader()
    import torch  # noqa: F401

    if payload.get("profile") == "complete":
        import torchaudio  # noqa: F401
        import pyannote.audio  # noqa: F401
        if payload.get("validate_diarization"):
            from audio import PyAnnoteSpeakerDiarizer

            diarizer = PyAnnoteSpeakerDiarizer(auth_token=token)
            diarizer._load_diarization_model()
            diarizer.offload_model()
    outcome = _batch_outcome()
    outcome["runtime"] = _runtime_metadata()
    return outcome


def _dispatch(operation: str, payload: dict[str, Any], cancelled: threading.Event, emit_progress, emit_status, token: str | None) -> dict[str, Any]:
    if operation == "probe":
        return _run_probe(payload, token)
    if operation == "extract_pose":
        return _run_pose(payload, cancelled, emit_progress, emit_status, embed=False)
    if operation == "embed_pose":
        return _run_pose(payload, cancelled, emit_progress, emit_status, embed=True)
    if operation == "extract_audio_features":
        return _run_audio_features(payload, cancelled, emit_progress, emit_status)
    if operation == "extract_transcripts":
        return _run_transcripts(payload, cancelled, emit_progress, emit_status, token)
    if operation == "align_features":
        return _run_alignment(payload, cancelled, emit_progress, emit_status)
    raise ValueError(f"Unknown worker operation: {operation}")


def _configure_worker_native_loader() -> None:
    """Install the MediaPipe-safe DLL phase after the worker—not wx—has started."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    from runtime_hook_dlls import configure_windows_dll_search_path

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root and os.path.isdir(bundle_root):
        configure_windows_dll_search_path(bundle_root, include_tensor_runtime=False)


def _enable_worker_tensor_loader() -> None:
    """Expose Torch DLLs only after MediaPipe has completed its initialization."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    from runtime_hook_dlls import configure_windows_dll_search_path

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root and os.path.isdir(bundle_root):
        configure_windows_dll_search_path(bundle_root, include_tensor_runtime=True)


def main() -> int:
    messages: queue.Queue[dict[str, Any]] = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    threading.Thread(target=reader, daemon=True).start()
    try:
        request = messages.get(timeout=30)
    except queue.Empty:
        return 2
    if request.get("protocol") != PROTOCOL_VERSION or request.get("type") != "run":
        return 2

    request_id = str(request.get("id") or "")
    operation = str(request.get("operation") or "")
    payload = dict(request.get("payload") or {})
    token = os.environ.pop(WORKER_TOKEN_ENV, None)
    _configure_worker_native_loader()
    try:
        with contextlib.redirect_stdout(_RedactingStream(sys.stderr, token)), contextlib.redirect_stderr(_RedactingStream(sys.stderr, token)):
            _configure_worker_ffmpeg()
            _initialize_worker_operation_runtime(operation, payload)
            _validate_worker_runtime_provenance(operation, payload)
    except Exception as exc:
        traceback.print_exc(file=_RedactingStream(sys.stderr, token))
        _emit(request_id, "error", message=_safe_error(exc, token))
        return 1
    cancelled = threading.Event()
    result_box: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def emit_progress(value: int) -> None:
        _emit(request_id, "progress", value=max(0, min(100, int(value))))

    def emit_status(message: str) -> None:
        _emit(request_id, "status", message=_safe_error(Exception(message), token))

    def run_operation() -> None:
        try:
            with contextlib.redirect_stdout(_RedactingStream(sys.stderr, token)), contextlib.redirect_stderr(_RedactingStream(sys.stderr, token)):
                result_box.put(("result", _dispatch(operation, payload, cancelled, emit_progress, emit_status, token)))
        except Exception as exc:
            traceback.print_exc(file=_RedactingStream(sys.stderr, token))
            result_box.put(("error", _safe_error(exc, token)))

    threading.Thread(target=run_operation, daemon=True).start()
    while True:
        try:
            kind, value = result_box.get(timeout=0.1)
            if kind == "result":
                _emit(request_id, "result", result=value)
                return 0
            _emit(request_id, "error", message=value)
            return 1
        except queue.Empty:
            pass
        while True:
            try:
                control = messages.get_nowait()
            except queue.Empty:
                break
            if control.get("protocol") == PROTOCOL_VERSION and control.get("id") == request_id and control.get("type") == "cancel":
                cancelled.set()


if __name__ == "__main__":
    raise SystemExit(main())
