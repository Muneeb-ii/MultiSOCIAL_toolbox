from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_embedded_worker_validator_uses_the_physical_site_packages_layout():
    validator = (ROOT / "packaging" / "validate_windows_bundle.py").read_text(encoding="utf-8")
    complete_layout = (
        ROOT / ".github" / "scripts" / "validate_complete_bundle_layout.py"
    ).read_text(encoding="utf-8")

    assert "Lib/site-packages/audresample/core/bin/win_amd64/audresample.dll" in validator
    assert "Lib/site-packages/opensmile/core/bin/win_amd64/SMILEapi.dll" in validator
    assert '"worker/assets/pose_landmark_heavy.tflite"' in complete_layout
