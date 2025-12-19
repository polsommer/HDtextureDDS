
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import List, Optional, Tuple


# ==========================================================
# PyInstaller-safe base directory
# ==========================================================
def frozen() -> bool:
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def pause(enabled: bool):
    if enabled or frozen():
        input("\nPress Enter to exit...")


def format_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


# ==========================================================
# SELF-HEALING TOOL DETECTION
# ==========================================================
def ensure_tools(tools_dir: Path) -> tuple[Path, Path, Path]:
    tools_dir = tools_dir.resolve()
    tools_dir.mkdir(exist_ok=True)

    texconv_candidates = [
        tools_dir / "texconv.exe",
        tools_dir / "TexConv.exe",
    ]
    texconv = next((p for p in texconv_candidates if p.exists()), None)
    if not texconv:
        raise RuntimeError(
            "\nMISSING: texconv.exe\n\n"
            "FIX:\n"
            "1. Download DirectXTex:\n"
            "   https://github.com/microsoft/DirectXTex/releases\n"
            "2. Copy texconv.exe (x64) into:\n"
            f"   {tools_dir}\n"
        )

    realesrgan_candidates = [
        tools_dir / "realesrgan-ncnn-vulkan.exe",
        tools_dir / "Real-ESRGAN-ncnn-vulkan.exe",
    ]
    realesrgan = next((p for p in realesrgan_candidates if p.exists()), None)
    if not realesrgan:
        raise RuntimeError(
            "\nMISSING: realesrgan-ncnn-vulkan.exe\n\n"
            "FIX:\n"
            "1. Download Real-ESRGAN (NCNN Vulkan):\n"
            "   https://github.com/xinntao/Real-ESRGAN/releases\n"
            "2. Copy realesrgan-ncnn-vulkan.exe into:\n"
            f"   {tools_dir}\n"
        )

    model_dirs = [
        tools_dir / "models",
        realesrgan.parent / "models",
    ]
    models = next((d for d in model_dirs if d.exists()), None)
    if not models:
        raise RuntimeError(
            "\nMISSING: models folder\n\n"
            "FIX:\n"
            "1. Open the Real-ESRGAN ZIP\n"
            "2. Copy the ENTIRE 'models' folder into:\n"
            f"   {tools_dir / 'models'}\n"
        )

    return texconv, realesrgan, models


# ==========================================================
# DDS helpers
# ==========================================================
def read_dds_size(path: Path) -> Tuple[int, int]:
    with path.open("rb") as f:
        if f.read(4) != b"DDS ":
            raise ValueError("Not DDS")
        header = f.read(124)
    h = struct.unpack_from("<I", header, 8)[0]
    w = struct.unpack_from("<I", header, 12)[0]
    return w, h


def is_normal_map(name: str) -> bool:
    n = name.lower()
    return any(x in n for x in ("_n.", "_nm.", "_normal.", "_norm."))


def choose_scale(w: int, h: int, max_dim: int) -> int:
    m = max(w, h)
    if m >= max_dim:
        return 1
    if m < 700:
        return 4
    if m < 1400:
        return 2
    return 1


# ==========================================================
# Data
# ==========================================================
@dataclass
class Result:
    src: str
    dst: str
    status: str
    width: int
    height: int
    scale: int
    kind: str
    message: Optional[str] = None


# ==========================================================
# CLI
# ==========================================================
def parse_args():
    root = base_dir()
    p = argparse.ArgumentParser("DDS → AI → DDS (RX 7600 / Vulkan)")
    p.add_argument("--input", type=Path, default=root / "texture")
    p.add_argument("--output", type=Path, default=root / "output")
    p.add_argument("--tools", type=Path, default=root / "tools")
    p.add_argument("--model", default="realesrgan-x4plus")
    p.add_argument("--gpu", type=int, default=0)
    p.add_argument("--max-dim", type=int, default=4096)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--pause", action="store_true")
    p.add_argument("--gui", action="store_true", help="Launch the Tkinter interface")
    return p.parse_args()


# ==========================================================
# Subprocess runner
# ==========================================================
def run(cmd: list[str], dry: bool) -> None:
    if dry:
        return
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)


# ==========================================================
# Pipeline
# ==========================================================
def dds_to_png(texconv: Path, src_dds: Path, tmp_dir: Path, dry: bool) -> Path:
    """
    texconv always outputs: <tmp_dir>/<src_stem>.png
    """
    run([str(texconv), "-ft", "png", "-y", "-o", str(tmp_dir), str(src_dds)], dry)
    return tmp_dir / f"{src_dds.stem}.png"


def upscale_png(
    realesrgan: Path,
    models_dir: Path,
    inp_png: Path,
    out_png: Path,
    scale: int,
    model: str,
    gpu: int,
    dry: bool,
) -> None:
    run(
        [
            str(realesrgan),
            "-i",
            str(inp_png),
            "-o",
            str(out_png),
            "-s",
            str(scale),
            "-n",
            model,
            "-m",
            str(models_dir),  # critical for EXE/CWD
            "-g",
            str(gpu),
            "-f",
            "png",
        ],
        dry,
    )


def png_to_dds(texconv: Path, src_png: Path, out_dir: Path, dds_format: str, dry: bool) -> Path:
    """
    texconv outputs: <out_dir>/<src_png_stem>.dds
    """
    run([str(texconv), "-y", "-f", dds_format, "-m", "0", "-o", str(out_dir), str(src_png)], dry)
    return out_dir / f"{src_png.stem}.dds"


# ==========================================================
# MAIN (CLI)
# ==========================================================
def run_pipeline(args) -> int:
    try:
        texconv, realesrgan, models = ensure_tools(args.tools)
    except Exception as exc:
        print(exc)
        pause(True)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    tmp = args.output / "_tmp"
    tmp.mkdir(exist_ok=True)

    files = list(args.input.rglob("*.dds"))
    print(f"Found {len(files)} DDS files")
    print(f"Input : {args.input.resolve()}")
    print(f"Output: {args.output.resolve()}")
    print(f"texconv: {texconv}")
    print(f"realesrgan: {realesrgan}")
    print(f"models: {models}")
    print(f"model: {args.model}\n")

    results: List[Result] = []
    start = datetime.utcnow().isoformat() + "Z"

    for i, src in enumerate(files, 1):
        rel = src.relative_to(args.input)
        dst = args.output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        print(f"[{i}/{len(files)}] {rel}")

        try:
            w, h = read_dds_size(src)
            kind = "normal" if is_normal_map(src.name) else "color"
            scale = 1 if kind == "normal" else choose_scale(w, h, args.max_dim)

            if dst.exists() and not args.overwrite:
                print("  SKIPPED")
                results.append(Result(str(src), str(dst), "skipped", w, h, scale, kind))
                continue

            # For normals, we copy (safe). You can change later if you want x2 normals.
            if scale == 1:
                if not args.dry_run:
                    shutil.copy2(src, dst)
                print("  OK (copy)")
                results.append(Result(str(src), str(dst), "ok", w, h, scale, kind, "copied"))
                continue

            # 1) DDS -> PNG
            png_in = dds_to_png(texconv, src, tmp, args.dry_run)

            # 2) AI upscale -> PNG (new name we control)
            png_out = tmp / f"{src.stem}_up.png"
            upscale_png(realesrgan, models, png_in, png_out, scale, args.model, args.gpu, args.dry_run)

            # 3) PNG -> DDS (writes <stem>_up.dds) then rename to mirrored path
            out_dds_dir = dst.parent
            fmt = "BC7_UNORM"  # color default
            produced_dds = png_to_dds(texconv, png_out, out_dds_dir, fmt, args.dry_run)

            # Rename produced file to desired mirrored name if needed
            if not args.dry_run:
                if produced_dds.resolve() != dst.resolve():
                    if dst.exists():
                        dst.unlink()
                    produced_dds.replace(dst)

            print(f"  OK (color, x{scale})")
            results.append(Result(str(src), str(dst), "ok", w, h, scale, kind))

        except Exception as exc:
            print(f"  ERROR: {exc}")
            # best effort width/height if available
            try:
                w, h = read_dds_size(src)
            except Exception:
                w, h = 0, 0
            results.append(Result(str(src), str(dst), "error", w, h, 0, "unknown", str(exc)))

    manifest = {
        "started": start,
        "finished": datetime.utcnow().isoformat() + "Z",
        "results": [asdict(r) for r in results],
    }
    (args.output / "processing_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = sum(1 for r in results if r.status == "error")

    print("\nDONE.")
    print(f"OK={ok}  SKIPPED={skipped}  ERRORS={errors}")
    print(f"Manifest: {args.output / 'processing_manifest.json'}")

    pause(args.pause)
    return 0 if errors == 0 else 2


# ==========================================================
# UI
# ==========================================================
class ProcessingUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DDS → AI → DDS")
        self.geometry("920x720")
        self.configure(background="#101820")

        defaults = base_dir()
        self.input_var = tk.StringVar(value=format_path(defaults / "texture"))
        self.output_var = tk.StringVar(value=format_path(defaults / "output"))
        self.tools_var = tk.StringVar(value=format_path(defaults / "tools"))
        self.model_var = tk.StringVar(value="realesrgan-x4plus")
        self.gpu_var = tk.IntVar(value=0)
        self.max_dim_var = tk.IntVar(value=4096)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.pause_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Idle")
        self.process: Optional[subprocess.Popen[str]] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.stop_requested = False

        self._configure_styles()
        self._build_layout()
        self._set_idle_state()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background="#101820")
        style.configure("App.TLabelframe", background="#101820", foreground="#c7ced4")
        style.configure("App.TLabelframe.Label", foreground="#4a90e2")
        style.configure("App.TLabel", background="#101820", foreground="#c7ced4")
        style.configure("App.TButton", background="#4a90e2", foreground="white", padding=6)
        style.map(
            "App.TButton",
            background=[("active", "#377ccf"), ("disabled", "#4a90e24d")],
            foreground=[("disabled", "#f0f4f8")],
        )

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=16, style="App.TFrame")
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container, style="App.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="DDS Batch Upscaler", font=("Segoe UI", 16, "bold"), style="App.TLabel").pack(
            anchor=tk.W
        )
        ttk.Label(
            header,
            text="Convert DDS → PNG, upscale with Real-ESRGAN, then write DDS outputs.",
            style="App.TLabel",
        ).pack(anchor=tk.W)

        paths = ttk.LabelFrame(container, text="Paths", padding=10, style="App.TLabelframe")
        paths.pack(fill=tk.X, expand=False, pady=(0, 10))
        self._add_path_row(paths, "Input", self.input_var)
        self._add_path_row(paths, "Output", self.output_var)
        self._add_path_row(paths, "Tools", self.tools_var)

        settings = ttk.LabelFrame(container, text="Settings", padding=10, style="App.TLabelframe")
        settings.pack(fill=tk.X, expand=False, pady=(0, 10))
        self._add_entry(settings, "Model", self.model_var)
        self._add_entry(settings, "GPU", self.gpu_var)
        self._add_entry(settings, "Max dimension", self.max_dim_var)

        flags = ttk.Frame(container, style="App.TFrame")
        flags.pack(fill=tk.X, expand=False, pady=(0, 10))
        ttk.Checkbutton(flags, text="Overwrite", variable=self.overwrite_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flags, text="Dry run", variable=self.dry_run_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flags, text="Pause on finish", variable=self.pause_var).pack(side=tk.LEFT, padx=4)

        status_frame = ttk.Frame(container, style="App.TFrame")
        status_frame.pack(fill=tk.X, expand=False, pady=(0, 10))
        ttk.Label(status_frame, text="Status:", style="App.TLabel").pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, style="App.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=6)

        logs = ttk.LabelFrame(container, text="Logs", padding=10, style="App.TLabelframe")
        logs.pack(fill=tk.BOTH, expand=True)
        self.log_widget = scrolledtext.ScrolledText(
            logs,
            height=18,
            wrap=tk.WORD,
            state=tk.DISABLED,
            background="#0f1620",
            foreground="#e5eef5",
            insertbackground="#4a90e2",
        )
        self.log_widget.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(container, style="App.TFrame")
        controls.pack(fill=tk.X, pady=(10, 0))
        self.start_button = ttk.Button(controls, text="Start", command=self.start_processing, style="App.TButton")
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop_processing, style="App.TButton")
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _add_path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=12, style="App.TLabel").pack(side=tk.LEFT)
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=lambda: self._choose_dir(variable), style="App.TButton").pack(
            side=tk.LEFT, padx=4
        )

    def _add_entry(self, parent: ttk.Frame, label: str, variable) -> None:
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=14, style="App.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _choose_dir(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory()
        if selected:
            variable.set(format_path(selected))

    def _log(self, message: str) -> None:
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def _set_running_state(self) -> None:
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.status_var.set("Running...")
        self.status_label.configure(foreground="#4a90e2")

    def _set_idle_state(self) -> None:
        self.start_button.state(["!disabled"])
        self.stop_button.state(["disabled"])
        self.status_var.set("Idle")
        self.status_label.configure(foreground="#c7ced4")

    def start_processing(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Processing", "A run is already in progress.")
            return

        command = [
            sys.executable,
            format_path(Path(__file__)),
            "--input",
            format_path(self.input_var.get()),
            "--output",
            format_path(self.output_var.get()),
            "--tools",
            format_path(self.tools_var.get()),
            "--model",
            self.model_var.get(),
            "--gpu",
            str(self.gpu_var.get()),
            "--max-dim",
            str(self.max_dim_var.get()),
        ]
        if self.overwrite_var.get():
            command.append("--overwrite")
        if self.dry_run_var.get():
            command.append("--dry-run")
        if self.pause_var.get():
            command.append("--pause")

        self._log("Running: " + " ".join(command))
        self.stop_requested = False

        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        except OSError as exc:
            self._log(f"Failed to start process: {exc}")
            return

        self._set_running_state()
        self.reader_thread = threading.Thread(target=self._stream_output, daemon=True)
        self.reader_thread.start()

    def stop_processing(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.stop_requested = True
        self.status_var.set("Stopping...")
        self.status_label.configure(foreground="#4a90e2")
        try:
            self.process.terminate()
        except OSError as exc:
            self._log(f"Failed to stop process: {exc}")

    def _stream_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            self.after(0, lambda msg=line.rstrip(): self._log(msg))
        code = self.process.wait()
        self.after(0, lambda: self._finish_run(code))

    def _finish_run(self, code: int) -> None:
        if code == 0:
            self.status_var.set("Done")
            self.status_label.configure(foreground="#3fb27f")
        elif self.stop_requested:
            self.status_var.set("Stopped")
            self.status_label.configure(foreground="#ff6b6b")
        else:
            self.status_var.set(f"Failed (code {code})")
            self.status_label.configure(foreground="#ff6b6b")

        self.process = None
        self.stop_requested = False
        self._set_idle_state()


def launch_gui() -> None:
    app = ProcessingUI()
    app.mainloop()


def main() -> int:
    args = parse_args()
    if args.gui:
        launch_gui()
        return 0
    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
