"""Lightweight Tkinter GUI wrapper for batch_process_dds.

The GUI exposes common inputs for batch processing DDS files and delegates the
actual work to ``scripts/batch_process_dds.py`` to preserve manifest generation
and processing semantics. Use this as a convenience layer when launching the
CLI locally.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import batch_process_dds  # noqa: E402


def format_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


class BatchGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DDS Batch Processor")
        self.geometry("820x720")

        self.model_name_var = tk.StringVar(value=batch_process_dds.DEFAULT_MODEL_NAME)
        self.model_cmd_var = tk.StringVar(
            value=batch_process_dds.DEFAULT_MODEL_CMD or "python -m your_upscaler --input {input} --output {output}"
        )
        self.input_var = tk.StringVar(value=str(Path("texture").resolve()))
        self.output_var = tk.StringVar(value=format_path(batch_process_dds.DEFAULT_OUTPUT_DIR))
        self.overwrite_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)

        self.git_commit_var = tk.BooleanVar(value=False)
        self.git_push_var = tk.BooleanVar(value=False)
        self.git_remote_var = tk.StringVar(value=batch_process_dds.DEFAULT_GIT_REMOTE)
        self.git_branch_var = tk.StringVar(value=batch_process_dds.DEFAULT_GIT_BRANCH)
        self.commit_message_var = tk.StringVar(value="Update processed DDS assets")

        self.file_count_var = tk.StringVar(value="Discovered files: 0")
        self.status_var = tk.StringVar(value="Idle")

        self._build_layout()
        self._update_file_count()

        self.process_thread: Optional[threading.Thread] = None
        self.progress_var = tk.DoubleVar(value=0)

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # Paths
        paths_frame = ttk.LabelFrame(container, text="Paths")
        paths_frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        self._add_entry_with_button(
            paths_frame, "Input directory", self.input_var, lambda: self._choose_dir(self.input_var)
        )
        self._add_entry_with_button(
            paths_frame, "Output directory", self.output_var, lambda: self._choose_dir(self.output_var)
        )

        # Model configuration
        model_frame = ttk.LabelFrame(container, text="Model")
        model_frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        self._add_labeled_entry(model_frame, "Model name", self.model_name_var)
        self._add_labeled_entry(model_frame, "Model command", self.model_cmd_var)

        # Flags
        flags_frame = ttk.Frame(container)
        flags_frame.pack(fill=tk.X, expand=False, pady=(0, 10))
        ttk.Checkbutton(flags_frame, text="Overwrite", variable=self.overwrite_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flags_frame, text="Dry run", variable=self.dry_run_var).pack(side=tk.LEFT, padx=4)

        # Git options
        git_frame = ttk.LabelFrame(container, text="Git options")
        git_frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Checkbutton(git_frame, text="Commit results", variable=self.git_commit_var).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(git_frame, text="Push after commit", variable=self.git_push_var).grid(row=0, column=1, sticky=tk.W)

        self._add_grid_entry(git_frame, "Remote", self.git_remote_var, row=1, column=0)
        self._add_grid_entry(git_frame, "Branch", self.git_branch_var, row=1, column=1)
        self._add_grid_entry(git_frame, "Commit message", self.commit_message_var, row=2, column=0, columnspan=2)

        # Status and controls
        status_frame = ttk.Frame(container)
        status_frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Label(status_frame, textvariable=self.file_count_var).pack(side=tk.LEFT)
        ttk.Button(status_frame, text="Refresh count", command=self._update_file_count).pack(side=tk.LEFT, padx=6)
        ttk.Label(status_frame, textvariable=self.status_var, foreground="blue").pack(side=tk.RIGHT)

        # Progress bar
        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill=tk.X, expand=False, pady=(0, 10))
        self.progress = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress.pack(fill=tk.X, expand=True)

        # Log output
        log_frame = ttk.LabelFrame(container, text="Logs")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_widget = tk.Text(log_frame, height=20, wrap=tk.WORD, state=tk.DISABLED)
        self.log_widget.pack(fill=tk.BOTH, expand=True)

        # Run button
        ttk.Button(container, text="Start processing", command=self.start_processing).pack(fill=tk.X, pady=(10, 0))

    def _add_entry_with_button(self, frame: ttk.Frame, label: str, variable: tk.StringVar, callback) -> None:
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=callback).pack(side=tk.LEFT, padx=4)

    def _add_labeled_entry(self, frame: ttk.Frame, label: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _add_grid_entry(
        self,
        frame: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        row: int,
        column: int,
        columnspan: int = 1,
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=column, sticky=tk.W, padx=4, pady=3)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=column + 1, sticky=tk.EW, padx=4, pady=3, columnspan=columnspan)
        frame.columnconfigure(column + 1, weight=1)

    def _choose_dir(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory()
        if selected:
            variable.set(format_path(selected))
            if variable is self.input_var:
                self._update_file_count()

    def _update_file_count(self) -> None:
        input_dir = Path(self.input_var.get())
        if not input_dir.exists():
            self.file_count_var.set("Discovered files: 0 (input missing)")
            return
        count = sum(1 for _ in batch_process_dds.discover_dds_files(input_dir))
        self.file_count_var.set(f"Discovered files: {count}")

    def log(self, message: str) -> None:
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def start_processing(self) -> None:
        if self.process_thread and self.process_thread.is_alive():
            messagebox.showinfo("Processing", "A run is already in progress.")
            return

        input_dir = Path(self.input_var.get())
        output_dir = Path(self.output_var.get())
        if not input_dir.exists():
            messagebox.showerror("Input missing", "The selected input directory does not exist.")
            return

        self.status_var.set("Running...")
        self.progress_var.set(0)
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.log("Starting batch_process_dds.py...")

        self.process_thread = threading.Thread(target=self._run_subprocess, daemon=True)
        self.process_thread.start()

    def _run_subprocess(self) -> None:
        command = self._build_command()
        self.log("Command: " + " ".join(command))
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(SCRIPT_DIR.parent),
                env=os.environ.copy(),
            )
        except OSError as exc:
            self.after(0, lambda: self._finish_run(error=str(exc)))
            return

        if process.stdout:
            for line in process.stdout:
                self.after(0, lambda msg=line.rstrip(): self.log(msg))

        process.wait()
        manifest_summary = self._read_manifest()
        if process.returncode == 0:
            self.after(0, lambda: self._finish_run(summary=manifest_summary))
        else:
            self.after(0, lambda: self._finish_run(error=f"Process exited with code {process.returncode}"))

    def _read_manifest(self) -> Optional[str]:
        output_dir = Path(self.output_var.get())
        manifest = output_dir / "processing_manifest.json"
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        total = len(data.get("processed", []))
        statuses = {}
        for item in data.get("processed", []):
            statuses[item.get("status", "unknown")] = statuses.get(item.get("status", "unknown"), 0) + 1
        parts = [f"Processed entries: {total}"]
        for key, value in sorted(statuses.items()):
            parts.append(f"{key}: {value}")
        return "; ".join(parts)

    def _build_command(self) -> list[str]:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "batch_process_dds.py"),
            "--input",
            format_path(self.input_var.get()),
            "--output",
            format_path(self.output_var.get()),
            "--model-name",
            self.model_name_var.get(),
        ]
        model_cmd = self.model_cmd_var.get().strip()
        if model_cmd:
            command.extend(["--model-cmd", model_cmd])
        if self.overwrite_var.get():
            command.append("--overwrite")
        if self.dry_run_var.get():
            command.append("--dry-run")
        if self.git_commit_var.get():
            command.append("--git-commit")
        if self.git_push_var.get():
            command.append("--git-push")
        if self.git_remote_var.get():
            command.extend(["--git-remote", self.git_remote_var.get()])
        if self.git_branch_var.get():
            command.extend(["--git-branch", self.git_branch_var.get()])
        if self.commit_message_var.get():
            command.extend(["--commit-message", self.commit_message_var.get()])
        return command

    def _finish_run(self, summary: Optional[str] = None, error: Optional[str] = None) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_var.set(100)

        if summary:
            self.log(summary)
        if error:
            self.log(error)
            self.status_var.set("Failed")
        else:
            self.status_var.set("Done")
        self._update_file_count()


def main() -> None:
    app = BatchGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
