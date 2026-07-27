"""Windows GUI backend that communicates only with the private worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import runtime_services
from native_worker_client import (
    NativeWorkerClient,
    WindowsAudioProcessor,
    WindowsPoseProcessor,
    find_pose_csv_paths as _find_pose_csv_paths,
)


class WindowsWorkerBackend:
    def create_audio_processor(self, *args, **kwargs):
        return WindowsAudioProcessor(*args, **kwargs)

    def create_pose_processor(self, *args, **kwargs):
        return WindowsPoseProcessor(*args, **kwargs)

    def find_pose_csv_paths(
        self,
        output_folder: str,
        video_path: str,
        multi_person: Optional[bool] = None,
    ) -> list[str]:
        return _find_pose_csv_paths(output_folder, video_path, multi_person)

    def validate_import_smoke(self, profile: str, verify_heavy_pose_asset: bool) -> str:
        if verify_heavy_pose_asset:
            if getattr(sys, "frozen", False):
                model = (
                    Path(sys.executable).resolve().parent
                    / "worker"
                    / "mediapipe"
                    / "modules"
                    / "pose_landmark"
                    / "pose_landmark_heavy.tflite"
                )
            else:
                model = Path(runtime_services.resource_path("assets", "pose_landmark_heavy.tflite"))
            if not model.is_file():
                raise RuntimeError(f"Missing bundled Heavy pose model: {os.path.basename(model)}")
            print("Bundled Heavy pose model check passed.", flush=True)
        if os.environ.get("MULTISOCIAL_VERIFY_WORKER_LAUNCH") == "1":
            result = NativeWorkerClient().run("probe", {"profile": profile})
            runtime = result.get("runtime") or {}
            worker_runtime = runtime.get("worker_runtime") or {}
            if (
                not worker_runtime.get("private_runtime")
                or worker_runtime.get("loaded_module_violations")
                or worker_runtime.get("native_module_violations")
            ):
                raise RuntimeError("Private worker runtime provenance check failed")
            print("GUI-to-worker isolated launch check passed.", flush=True)
        return f"Import smoke test passed ({profile or 'standard'} profile)."
