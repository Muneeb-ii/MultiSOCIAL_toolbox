"""Assemble two finished onedir applications without merging their TOCs."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _copy_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def assemble(gui: Path, worker: Path, output: Path) -> None:
    if not gui.is_dir() or not worker.is_dir():
        raise FileNotFoundError("Both independent PyInstaller onedir outputs are required")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(gui, output)
    _copy_contents(worker, output / "worker")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.gui.resolve(), args.worker.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
