# HDtextureDDS

Batch processing and upscaling helpers for the DDS texture collection contained
in this repository. Use the provided script, GitHub Actions workflow, and Slurm
job template to automate runs locally, in CI, or on the SWG-RPI-Cluster.

## Batch-processing script

`scripts/batch_process_dds.py` scans the `texture/` directory (or a custom
input) for `.dds` files, runs them through a model command, and mirrors the
outputs into a dedicated output directory. A `processing_manifest.json` is
written alongside the results to capture run metadata.

- Normal maps (filenames containing `_n.`, `_nm.`, `_normal.`, `_norm.`) are
  detected automatically and copied without model invocation.
- Color textures use resolution-based scales: `<700px` → x4, `<1400px` → x2,
  otherwise copy. Anything at or above `--max-dim` (default `4096` or
  `DDS_MAX_DIM`) is copied for safety.
- Per-file width/height, kind, and chosen scale are printed and stored in the
  manifest for auditing.

Example (copy-only fallback):

```bash
python scripts/batch_process_dds.py \
  --input texture \
  --output output \
  --model-name custom-model
```

Example (external upscaler command):

```bash
export DDS_MODEL_CMD="python -m your_upscaler --input {input} --output {output}"
python scripts/batch_process_dds.py --input texture --output output --model-name esrgan
```

Example (command aware of scale/kind via placeholders and env vars):

```bash
export DDS_MODEL_CMD="python -m your_upscaler --input {input} --output {output} --scale {scale} --tag {kind}"
python scripts/batch_process_dds.py --input texture --output output --model-name esrgan --max-dim 4096
```

The script also exports `DDS_WIDTH`, `DDS_HEIGHT`, `DDS_SCALE`, and `DDS_KIND`
to the processing command's environment so external tooling can branch on the
detected metadata.

Optional git automation (requires `git config user.name`/`user.email`):

```bash
python scripts/batch_process_dds.py --output output --git-commit --git-push \
  --git-remote origin --git-branch main --commit-message "Add processed DDS"
```

Key flags:

- `--model-cmd`: Command template using `{input}` and `{output}` placeholders.
- `--max-dim`: Cap resolution; files at/above this size are copied and not
  upscaled.
- `--overwrite`: Replace existing files in the output tree.
- `--dry-run`: Print planned commands without executing them.
- `--git-commit/--git-push`: Optional archival of outputs back to GitHub.

Additional templating placeholders available to model commands: `{scale}`
(chosen multiplier), `{kind}` ("normal"/"color"), `{width}`, `{height}`.

The script respects several environment variables (CLI flags take priority):

- `DDS_MODEL_CMD`: Default model command template.
- `DDS_MODEL_NAME`: Label stored in the manifest for the model used.
- `DDS_OUTPUT_DIR`: Default output folder (defaults to `output/`).
- `DDS_GIT_REMOTE` / `DDS_GIT_BRANCH`: Defaults for push targets.
- `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`: Recommended when committing in CI.
- `GITHUB_TOKEN`: Required by GitHub Actions when push is requested.

GPU devices are masked by default (`CUDA_VISIBLE_DEVICES`/`ROCM_VISIBLE_DEVICES`
set to empty) so processing runs on CPUs. Set those variables explicitly before
invoking the script if accelerator access is desired.

### Windows single-file executable

To avoid setting up Python on Windows, bundle the script into a standalone
`.exe` using PyInstaller:

1. Install [Python 3.10+ for Windows](https://www.python.org/downloads/windows/)
   and add it to `PATH`.
2. Install PyInstaller: `python -m pip install pyinstaller`.
3. From the repository root, run:

   ```powershell
   python scripts/build_windows_exe.py
   ```

The executable will be written to `dist/batch_process_dds.exe`. Use it with the
same CLI flags as the Python script, for example:

```powershell
dist\batch_process_dds.exe --input texture --output output --model-name custom-model
```

## GitHub Actions workflow

`.github/workflows/process-dds.yml` exposes a `workflow_dispatch` entrypoint so
runs are manual and configurable. Inputs include the model command, output
folder, overwrite toggle, and git commit/push options. The workflow:

1. Checks out the repo and prepares Python 3.11.
2. Configures git identity when commits/pushes are requested.
3. Invokes `scripts/batch_process_dds.py` with the provided inputs. Set
   `model_cmd` to the full processing CLI (must contain `{input}` and
   `{output}` placeholders) and ensure `GITHUB_TOKEN` has `contents:write`
   permissions when enabling pushes.

## SWG-RPI-Cluster job template

`cluster/swg_rpi_job.sh` is a Slurm submission script tuned for the
SWG-RPI-Cluster CPU partition. Default resources request 8 CPUs, 32 GB RAM, and
an 8-hour wall clock. Edit the `#SBATCH` lines as needed.

Usage:

```bash
# Optional: point to an existing virtual environment
export DDS_VENV="$HOME/venvs/dds"

# Configure model invocation and destination
export DDS_MODEL_NAME="esrgan"
export DDS_MODEL_CMD="python -m your_upscaler --input {input} --output {output}"
export DDS_OUTPUT_DIR="$PWD/output"
export DDS_OVERWRITE=1  # optional

# Optional git archival
export DDS_GIT_COMMIT=1
export DDS_GIT_PUSH=1
export DDS_GIT_REMOTE=origin
export DDS_GIT_BRANCH=main
export GIT_AUTHOR_NAME="Cluster Bot"
export GIT_AUTHOR_EMAIL="bot@example.com"

sbatch cluster/swg_rpi_job.sh
```

Dependencies on the cluster:

- Python 3.10+ (loaded via `module load python/3.10`).
- Any model-specific wheels or binaries available in the active environment
  (`DDS_VENV` is respected when set). GPU drivers are not required; the
  submission script masks accelerators so processing stays on CPUs.
- Git credentials if committing/pushing from the job (e.g., SSH agent or
  `GITHUB_TOKEN` with `git remote set-url`).

## Output and manifests

Processed files mirror the input directory structure under the chosen output
folder. Each run produces `processing_manifest.json` capturing timestamps,
model metadata, and per-file status (ok/skipped/error) to assist with audits and
reruns.
