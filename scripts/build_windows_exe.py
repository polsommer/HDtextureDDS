"""Create a standalone Windows executable for the batch DDS processor.

This helper wraps PyInstaller so the main script can be shipped as a single
`.exe` without requiring Python to be preinstalled. Run it on Windows with
Python 3.10+ and PyInstaller available in the environment.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package batch_process_dds.py into a single-file Windows executable.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to invoke PyInstaller with (defaults to the current interpreter).",
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="Destination directory for the generated executable.",
    )
    parser.add_argument(
        "--name",
        default="batch_process_dds",
        help="Name of the output executable (without extension).",
    )
    parser.add_argument(
        "--script",
        default=Path("scripts") / "batch_process_dds.py",
        type=Path,
        help="Path to the processing script to bundle.",
    )
    return parser.parse_args()


def ensure_pyinstaller_available(python: str) -> None:
    if shutil.which(python) is None:
        raise RuntimeError(f"Python interpreter not found: {python}")

    try:
        subprocess.run(
            [python, "-m", "PyInstaller", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "PyInstaller is required to build the executable. Install it with "
            "'pip install pyinstaller' inside your Windows environment."
        ) from exc


def build_executable(args: argparse.Namespace) -> Path:
    script_path = args.script.resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    ensure_pyinstaller_available(args.python)

    command = [
        args.python,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        args.name,
        "--distpath",
        args.dist_dir,
        str(script_path),
    ]

    result = subprocess.run(command, check=True, text=True, capture_output=True)
    print(result.stdout)
    return Path(args.dist_dir) / f"{args.name}.exe"


def main() -> int:
    args = parse_args()
    executable = build_executable(args)
    print(f"Executable created at: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
