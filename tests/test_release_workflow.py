from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_supports_main_and_tag_pushes():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "branches:" in workflow
    assert "- main" in workflow
    assert 'tags:' in workflow
    assert '- "v*"' in workflow


def test_release_workflow_has_upstream_auto_release_guardrails():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "UPSTREAM_REPOSITORY: Tahiya31/MultiSOCIAL_toolbox" in workflow
    assert "should_publish_release" in workflow
    assert "Create upstream release record" in workflow
    assert "Release ${tag} already exists. Bump pyproject.toml before merging to main." in workflow
    assert "Git tag ${tag} already exists. Bump pyproject.toml before merging to main." in workflow


def test_release_workflow_uploads_versioned_artifacts():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "name: MultiSOCIAL-${{ env.APP_VERSION }}-${{ matrix.profile }}-${{ runner.os }}" in workflow
    assert "Upload release assets to upstream release" in workflow
    assert "tag_name: ${{ needs.prepare.outputs.release_tag }}" in workflow


def test_windows_release_builds_install_only_from_committed_lock():
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    standard_lock = (REPO_ROOT / "requirements" / "locks" / "windows-standard-py310.txt").read_text(encoding="utf-8")
    complete_lock = (REPO_ROOT / "requirements" / "locks" / "windows-complete-py310.txt").read_text(encoding="utf-8")
    complete_bootstrap = (REPO_ROOT / "requirements" / "locks" / "windows-complete-bootstrap.txt").read_text(encoding="utf-8")

    assert "Verify committed Windows release lock" in workflow
    assert "pip install --require-hashes -r" in workflow
    assert "windows-${{ matrix.profile }}-py310.txt" in workflow
    assert "windows-2025" in workflow
    assert "windows-client-compat" in workflow
    assert "windows-2022" in workflow
    assert "Download release-built Windows bundle" in workflow
    assert "Run downloaded Windows worker probe and audio smoke" in workflow
    assert "--hash=sha256:" in standard_lock
    assert "--hash=sha256:" in complete_lock
    assert "windows-complete-bootstrap.txt" in complete_lock
    assert "pyannote.core==5.0.0" in complete_bootstrap
