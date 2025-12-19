
"""
Create a standalone Windows executable for the batch DDS processor.

This helper wraps PyInstaller so the main script can be shipped as a single
.exe without requiring Python to be preinstalled.

Run on Windows with Python 3.10+ and PyInstaller installed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


# Always resolve paths relative to this file, not the working directory
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package batch_process_dds.py into a single-file Windows executable.",
    )

    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to invoke PyInstaller with "
             "(defaults to the current interpreter).",
    )

    parser.add_argument(
        "--dist-dir",
        default=PROJECT_ROOT / "dist",
        type=Path,
        help="Destination directory for the generated executable.",
    )

    parser.add_argument(
        "--name",
        default="batch_process_dds",
        help="Name of the output executable (without extension).",
    )

    parser.add_argument(
        "--script",
        default=SCRIPT_DIR / "batch_process_dds.py",
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
            "PyInstaller is required to build the executable.\n"
            "Install it with:\n\n"
            "    pip install pyinstaller\n"
        ) from exc


def build_executable(args: argparse.Namespace) -> Path:
    script_path = args.script.resolve()

    if not script_path.exists():
        raise FileNotFoundError(
            f"Target script not found:\n  {script_path}"
        )

    ensure_pyinstaller_available(args.python)

    args.dist_dir.mkdir(parents=True, exist_ok=True)

    command = [
        args.python,
        "-m", "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--name", args.name,
        "--distpath", str(args.dist_dir),
        str(script_path),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
        )
        print(result.stdout)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr, file=sys.stderr)
        raise

    return args.dist_dir / f"{args.name}.exe"


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("This build script must be run on Windows.")

    args = parse_args()
    executable = build_executable(args)
    print(f"\n✅ Executable created at:\n{executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
