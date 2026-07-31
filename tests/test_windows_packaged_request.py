from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _request_runner():
    spec = importlib.util.spec_from_file_location(
        "run_windows_packaged_request",
        REPO_ROOT / ".github" / "scripts" / "run_windows_packaged_request.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_timeline_emits_only_stage_names_and_elapsed_times(tmp_path):
    diagnostic = tmp_path / "worker-diagnostics.jsonl"
    diagnostic.write_text(
        '\n'.join(
            (
                '{"stage":"boot","elapsed_ms":4,"path":"C:\\\\private"}',
                '{"stage":"preload:mediapipe","elapsed_ms":1234,"token":"secret"}',
                '{"stage":"ignored","elapsed_ms":"not-a-number"}',
            )
        ),
        encoding="utf-8",
    )

    assert _request_runner()._diagnostic_timeline(diagnostic) == [
        {"stage": "boot", "elapsed_ms": 4},
        {"stage": "preload:mediapipe", "elapsed_ms": 1234},
    ]
