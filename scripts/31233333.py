
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
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
# MAIN
# ==========================================================
def main() -> int:
    args = parse_args()

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


if __name__ == "__main__":
    raise SystemExit(main())
