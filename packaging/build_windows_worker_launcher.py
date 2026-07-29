"""Build the static-CRT native trampoline used by Windows worker packages."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "windows_worker_launcher.c"


def _developer_command(output: Path) -> str:
    program_files = Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
    vswhere = program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise FileNotFoundError("Visual Studio Build Tools locator is unavailable")
    installation = subprocess.check_output(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        text=True,
        encoding="utf-8",
    ).strip()
    vcvars = Path(installation) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not installation or not vcvars.is_file():
        raise RuntimeError("Visual Studio x64 C++ build tools are unavailable")
    # /MT avoids any client-side Visual C++ redistributable dependency.
    return (
        f'call "{vcvars}" >nul && '
        f'cl /nologo /std:c11 /O2 /W4 /WX /MT /DUNICODE /D_UNICODE '
        f'/Fe"{output}" "{SOURCE}" /link /SUBSYSTEM:CONSOLE'
    )


def build(output: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("The native worker launcher can only be built on Windows")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd.exe", "/d", "/s", "/c", _developer_command(output)], check=True)
    if not output.is_file():
        raise RuntimeError("Native worker launcher build did not produce an executable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
