"""Batch-processing utility for DDS files.

This script scans an input directory for ``.dds`` files, runs them through a
user-provided processing command (e.g., an upscaler), and writes results to a
mirrored path under the output directory. It can optionally commit/push results
back to GitHub for archival.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import struct
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# Default to CPU execution by hiding accelerator devices from common runtimes
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("ROCM_VISIBLE_DEVICES", "")


@dataclass
class ProcessingResult:
    """Metadata for a single processed file."""

    source: str
    output: str
    status: str
    width: int
    height: int
    scale: int
    kind: str
    message: Optional[str] = None


@dataclass
class RunSummary:
    model_name: str
    model_command: Optional[str]
    input_root: str
    output_root: str
    processed: List[ProcessingResult]
    started_at: str
    finished_at: str
    dry_run: bool
    overwrite: bool


DEFAULT_MODEL_NAME = os.environ.get("DDS_MODEL_NAME", "custom-model")
DEFAULT_MODEL_CMD = os.environ.get("DDS_MODEL_CMD")
DEFAULT_GIT_REMOTE = os.environ.get("DDS_GIT_REMOTE", "origin")
DEFAULT_GIT_BRANCH = os.environ.get("DDS_GIT_BRANCH", "main")
DEFAULT_OUTPUT_DIR = os.environ.get("DDS_OUTPUT_DIR", "output")
DEFAULT_MAX_DIM = int(os.environ.get("DDS_MAX_DIM", "4096"))


def read_dds_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        if f.read(4) != b"DDS ":
            raise ValueError("Not a DDS file")
        header = f.read(124)
    height = struct.unpack_from("<I", header, 8)[0]
    width = struct.unpack_from("<I", header, 12)[0]
    return width, height


def is_normal_map(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("_n.", "_nm.", "_normal.", "_norm."))


def choose_scale(width: int, height: int, max_dim: int) -> int:
    largest = max(width, height)
    if largest >= max_dim:
        return 1
    if largest < 700:
        return 4
    if largest < 1400:
        return 2
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-process DDS files with a configurable model command.",
    )
    parser.add_argument(
        "--input",
        dest="input_dir",
        type=Path,
        default=Path("texture"),
        help="Directory containing DDS files to process.",
    )
    parser.add_argument(
        "--output",
        dest="output_dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help="Directory where processed files will be written.",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Label used in the manifest to describe the model in use.",
    )
    parser.add_argument(
        "--model-cmd",
        default=DEFAULT_MODEL_CMD,
        help=(
            "Command template for processing a single DDS file. Use '{input}' and "
            "'{output}' as placeholders. If omitted, files are copied instead."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace outputs that already exist.",
    )
    parser.add_argument(
        "--max-dim",
        type=int,
        default=DEFAULT_MAX_DIM,
        help=(
            "Largest dimension threshold used to pick scale factors."
            " Values at or above this dimension are copied instead of upscaled."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands without executing them.",
    )
    parser.add_argument(
        "--git-commit",
        action="store_true",
        help="Commit the output directory after processing.",
    )
    parser.add_argument(
        "--git-push",
        action="store_true",
        help="Push the commit to the remote after committing (implies --git-commit).",
    )
    parser.add_argument(
        "--git-remote",
        default=DEFAULT_GIT_REMOTE,
        help="Remote name to push to when --git-push is set.",
    )
    parser.add_argument(
        "--git-branch",
        default=DEFAULT_GIT_BRANCH,
        help="Branch name to push to when --git-push is set.",
    )
    parser.add_argument(
        "--commit-message",
        default="Update processed DDS assets",
        help="Commit message used when --git-commit is set.",
    )
    return parser.parse_args()


def discover_dds_files(root: Path) -> Iterable[Path]:
    return root.rglob("*.dds")


def run_command(command: str, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        shell=True,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def process_file(
    source: Path,
    output: Path,
    command_template: Optional[str],
    dry_run: bool,
    overwrite: bool,
    width: int,
    height: int,
    scale: int,
    kind: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> ProcessingResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="skipped",
            width=width,
            height=height,
            scale=scale,
            kind=kind,
            message="Output exists; use --overwrite to reprocess",
        )

    if scale == 1:
        if dry_run:
            return ProcessingResult(
                source=str(source),
                output=str(output),
                status="pending",
                width=width,
                height=height,
                scale=scale,
                kind=kind,
                message="copy",
            )
        output.write_bytes(source.read_bytes())
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="ok",
            width=width,
            height=height,
            scale=scale,
            kind=kind,
            message="copied",
        )

    format_kwargs = {
        "input": str(source),
        "output": str(output),
        "scale": scale,
        "kind": kind,
        "width": width,
        "height": height,
    }

    if command_template:
        command = command_template.format(**format_kwargs)
        if dry_run:
            return ProcessingResult(
                source=str(source),
                output=str(output),
                status="pending",
                width=width,
                height=height,
                scale=scale,
                kind=kind,
                message=command,
            )
        try:
            result = run_command(command, env=extra_env)
        except subprocess.CalledProcessError as exc:
            return ProcessingResult(
                source=str(source),
                output=str(output),
                status="error",
                width=width,
                height=height,
                scale=scale,
                kind=kind,
                message=exc.stderr or exc.stdout,
            )
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="ok",
            width=width,
            height=height,
            scale=scale,
            kind=kind,
            message=result.stdout.strip(),
        )

    if dry_run:
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="pending",
            width=width,
            height=height,
            scale=scale,
            kind=kind,
            message="copy",
        )
    output.write_bytes(source.read_bytes())
    return ProcessingResult(
        source=str(source),
        output=str(output),
        status="ok",
        width=width,
        height=height,
        scale=scale,
        kind=kind,
        message="copied",
    )


def write_manifest(summary: RunSummary, output_dir: Path) -> None:
    manifest_path = output_dir / "processing_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_name": summary.model_name,
                "model_command": summary.model_command,
                "input_root": summary.input_root,
                "output_root": summary.output_root,
                "started_at": summary.started_at,
                "finished_at": summary.finished_at,
                "dry_run": summary.dry_run,
                "overwrite": summary.overwrite,
                "processed": [asdict(item) for item in summary.processed],
            },
            indent=2,
        )
    )


def commit_and_push(output_dir: Path, message: str, remote: str, branch: str, push: bool) -> None:
    run_command(f"git add {shlex.quote(str(output_dir))}")
    run_command(f"git commit -m {shlex.quote(message)}")
    if push:
        run_command(f"git push {shlex.quote(remote)} {shlex.quote(branch)}")


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    command_template = args.model_cmd

    dds_files = list(discover_dds_files(input_dir))
    results: List[ProcessingResult] = []
    base_env = os.environ.copy()

    start = datetime.utcnow().isoformat() + "Z"
    for idx, source in enumerate(dds_files, 1):
        rel = source.relative_to(input_dir)
        output = output_dir / rel
        try:
            width, height = read_dds_size(source)
        except Exception:
            width, height = 0, 0
        kind = "normal" if is_normal_map(source.name) else "color"
        scale = 1 if kind == "normal" else choose_scale(width, height, args.max_dim)
        print(
            f"[{idx}/{len(dds_files)}] {rel} -> {width}x{height} "
            f"kind={kind} scale=x{scale}"
        )
        env = base_env.copy()
        env.update(
            {
                "DDS_KIND": kind,
                "DDS_SCALE": str(scale),
                "DDS_WIDTH": str(width),
                "DDS_HEIGHT": str(height),
            }
        )
        result = process_file(
            source=source,
            output=output,
            command_template=command_template,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            width=width,
            height=height,
            scale=scale,
            kind=kind,
            extra_env=env,
        )
        results.append(result)
        print(f"  status={result.status} message={result.message or ''}\n")

    finish = datetime.utcnow().isoformat() + "Z"
    summary = RunSummary(
        model_name=args.model_name,
        model_command=command_template,
        input_root=str(input_dir),
        output_root=str(output_dir),
        processed=results,
        started_at=start,
        finished_at=finish,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(summary, output_dir)

    if args.git_push:
        args.git_commit = True
    if args.git_commit:
        commit_and_push(
            output_dir=output_dir,
            message=args.commit_message,
            remote=args.git_remote,
            branch=args.git_branch,
            push=args.git_push,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
