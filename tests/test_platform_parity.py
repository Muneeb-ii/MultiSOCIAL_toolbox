from __future__ import annotations

import inspect
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from atomic_outputs import OutputStaging
from native_worker_client import WindowsAudioProcessor, find_pose_csv_paths as worker_pose_paths


def test_output_staging_promotes_complete_files_and_discards_cancelled_work(tmp_path):
    destination = tmp_path / "outputs"
    transaction = OutputStaging(destination)
    with transaction as staged:
        (staged / "complete.csv").write_text("complete", encoding="utf-8")
        transaction.commit()

    assert (destination / "complete.csv").read_text(encoding="utf-8") == "complete"
    assert not list(destination.glob(".multisocial-stage-*"))

    with OutputStaging(destination) as staged:
        (staged / "cancelled.csv").write_text("partial", encoding="utf-8")

    assert not (destination / "cancelled.csv").exists()
    assert not list(destination.glob(".multisocial-stage-*"))


def test_worker_pose_csv_resolution_is_precise_and_mode_compatible(tmp_path):
    for name in (
        "clip_ID_0.csv",
        "clip_multi_ID_0.csv",
        "clip2_ID_0.csv",
    ):
        (tmp_path / name).write_text("frame,person_id\n", encoding="utf-8")
    video = tmp_path / "clip.mp4"

    assert worker_pose_paths(str(tmp_path), str(video), None) == [
        str(tmp_path / "clip_multi_ID_0.csv")
    ]
    assert worker_pose_paths(str(tmp_path), str(video), False) == [
        str(tmp_path / "clip_ID_0.csv")
    ]
    assert worker_pose_paths(str(tmp_path), str(video), True) == [
        str(tmp_path / "clip_multi_ID_0.csv")
    ]


def test_single_transcript_call_signature_has_the_same_argument_order(import_audio):
    audio = import_audio
    native = list(inspect.signature(audio.AudioProcessor.extract_transcript).parameters)
    worker = list(inspect.signature(WindowsAudioProcessor.extract_transcript).parameters)

    assert native == ["self", "filepath", "progress_callback", "word_timestamps", "cancel_check"]
    assert worker == ["self", "audio_file", "progress_callback", "word_timestamps", "cancel_check"]
