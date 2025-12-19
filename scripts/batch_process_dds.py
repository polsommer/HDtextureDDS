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
    extra_env: Optional[Dict[str, str]] = None,
) -> ProcessingResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="skipped",
            message="Output exists; use --overwrite to reprocess",
        )

    if command_template:
        command = command_template.format(input=str(source), output=str(output))
        if dry_run:
            return ProcessingResult(
                source=str(source),
                output=str(output),
                status="pending",
                message=command,
            )
        try:
            result = run_command(command, env=extra_env)
        except subprocess.CalledProcessError as exc:
            return ProcessingResult(
                source=str(source),
                output=str(output),
                status="error",
                message=exc.stderr or exc.stdout,
            )
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="ok",
            message=result.stdout.strip(),
        )

    if dry_run:
        return ProcessingResult(
            source=str(source),
            output=str(output),
            status="pending",
            message="copy",
        )
    output.write_bytes(source.read_bytes())
    return ProcessingResult(source=str(source), output=str(output), status="ok", message="copied")


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

    start = datetime.utcnow().isoformat() + "Z"
    for source in dds_files:
        rel = source.relative_to(input_dir)
        output = output_dir / rel
        result = process_file(
            source=source,
            output=output,
            command_template=command_template,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            extra_env=os.environ.copy(),
        )
        results.append(result)

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
