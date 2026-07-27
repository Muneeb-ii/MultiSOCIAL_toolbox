"""Run one worker request through the packaged, native-free Windows GUI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

def run_request(
    gui: Path,
    request: Path,
    result: Path,
    *,
    verify_heavy_pose_asset: bool = False,
    timeout: int = 1800,
) -> dict:
    gui = gui.resolve()
    request = request.resolve()
    result = result.resolve()
    if not gui.is_file() or not request.is_file():
        raise FileNotFoundError("The packaged GUI and request JSON must both exist")
    result.unlink(missing_ok=True)

    environment = os.environ.copy()
    environment["MULTISOCIAL_IMPORT_SMOKE_TEST"] = "1"
    environment["MULTISOCIAL_WORKER_SMOKE_REQUEST"] = str(request)
    environment["MULTISOCIAL_WORKER_SMOKE_RESULT"] = str(result)
    if verify_heavy_pose_asset:
        environment["MULTISOCIAL_VERIFY_HEAVY_POSE_ASSET"] = "1"

    completed = subprocess.run(
        [str(gui)],
        cwd=str(gui.parent),
        env=environment,
        timeout=timeout,
        check=False,
    )
    if not result.is_file():
        raise RuntimeError(
            f"Packaged GUI exited with {completed.returncode} without a structured result"
        )
    response = json.loads(result.read_text(encoding="utf-8"))
    if completed.returncode != 0 or not response.get("ok"):
        raise RuntimeError(
            f"Packaged request failed (exit {completed.returncode}): "
            f"{response.get('error', response)!s}"
        )
    return response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--verify-heavy-pose-asset", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    response = run_request(
        args.gui,
        args.request,
        args.result,
        verify_heavy_pose_asset=args.verify_heavy_pose_asset,
        timeout=args.timeout,
    )
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
