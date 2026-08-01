"""Small file-output transaction used by native analysis operations."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class OutputStaging:
    """Stage complete output files beside their destination, then promote them.

    A cancellation or failed operation removes the private staging directory.
    Promotion happens only after all required files have been written, so users
    never see a truncated CSV, transcript sidecar, or video at its final name.
    """

    def __init__(self, destination: str | os.PathLike[str]):
        self.destination = Path(destination)
        self.path: Path | None = None
        self._committed = False

    def __enter__(self) -> Path:
        self.destination.mkdir(parents=True, exist_ok=True)
        self.path = Path(
            tempfile.mkdtemp(prefix=".multisocial-stage-", dir=self.destination)
        )
        return self.path

    def commit(self) -> None:
        if self.path is None:
            raise RuntimeError("Output staging has not been opened")
        for staged_path in self.path.iterdir():
            os.replace(staged_path, self.destination / staged_path.name)
        self._committed = True

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.path is not None and (exc_type is not None or not self._committed):
            shutil.rmtree(self.path, ignore_errors=True)
        elif self.path is not None:
            self.path.rmdir()
