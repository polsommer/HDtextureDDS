# HDtextureDDS

## Project Overview
HDtextureDDS collects and manages high-definition DDS (DirectDraw Surface) texture replacements for Star Wars Galaxies assets. The repository provides curated texture packs, guidance for applying them to game assets, and a blueprint for automated AI-driven upscaling to enhance visual fidelity while keeping formats compatible with existing tools.

## Applying DDS Textures
1. **Locate target assets**: Identify the game asset or mod folder that consumes DDS textures (e.g., `texture/` inside your SWG installation or mod workspace).
2. **Back up originals**: Copy the original DDS files to a safe location before overwriting.
3. **Copy replacements**: Place the new DDS files from this repository into the corresponding asset paths. Preserve filenames so the engine can resolve the textures without additional configuration.
4. **Verify in-engine**: Launch the game or asset viewer and confirm the textures load without artifacts or format errors (DXT1/DXT5 channels, normal map orientations, mipmaps).
5. **Fallback plan**: If a texture appears corrupted, revert to the backup and check that your tooling preserves the DDS header, mipmaps, and compression format.

## Proposed AI-Driven Upscaling & Analysis Pipeline
The following pipeline outlines how to upscale and validate DDS textures while keeping outputs reproducible:
- **Ingest**: Read source DDS files and convert them to a lossless working format (e.g., PNG or EXR) while retaining alpha and channel order.
- **Preflight analysis**: Run channel inspection (alpha, roughness/metalness if packed), resolution checks, and color-space tagging (sRGB vs. linear) to detect problematic assets before upscaling.
- **AI upscaling**: Use a model such as ESRGAN or Real-ESRGAN with domain-specific finetuning for SWG assets. Target 2×–4× scale, respecting normal-map rules (do not denoise normal maps with color models; use a normal-map–aware model instead).
- **Post-processing**: Re-apply DDS compression (DXT1/DXT5/BC7 depending on alpha), generate mipmaps, and preserve original filenames. Validate channel integrity and normal-map length preservation.
- **Automated QA**: Run PSNR/SSIM metrics against the source, surface normal checks, and quick in-engine preview thumbnails to spot regressions.
- **Packaging**: Store the upscaled outputs under a versioned directory (see "Upscaled Output Storage") and emit a manifest summarizing resolutions, compression, and hashes for reproducibility.

## Running the Pipeline on the SWG-RPI-Cluster
Below is a suggested workflow for running the upscaling pipeline on the SWG-RPI-Cluster. Adapt paths as needed for your cluster layout.

### Prerequisites
- Python 3.10+ and `pip` available on cluster nodes.
- GPU-enabled nodes with CUDA/cuDNN drivers if using GPU-accelerated upscalers.
- Access to the shared filesystem where the `texture/` directory and outputs will live.
- Git access (with SSH keys or tokens) to push results.

### Setup Commands
```bash
# 1) Clone the repository on the cluster login node
git clone https://github.com/your-org/HDtextureDDS.git
cd HDtextureDDS

# 2) Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 3) Install required tools (example: Real-ESRGAN + DDS utilities)
pip install real-esrgan Pillow imageio
# If you use bc7enc or texconv, install or load the relevant modules
# module load bc7enc
# module load texconv

# 4) Prepare input/output directories on shared storage
mkdir -p texture/input texture/upscaled texture/manifests

# 5) Run the upscaling job (example invocation)
python scripts/upscale.py \
  --input texture/input \
  --output texture/upscaled \
  --model realesrgan-x4plus.pth \
  --format dds \
  --manifest texture/manifests/latest.json
```

### Expected Outputs
- Upscaled DDS files in `texture/upscaled/` mirroring the input directory structure.
- A JSON or CSV manifest in `texture/manifests/` summarizing resolutions, formats, hashes, and timestamps.
- Console logs indicating processed files, skipped assets, and any validation warnings.

## Upscaled Output Storage
- **Location**: By default, store generated DDS files under `texture/upscaled/` and preserve relative paths to match the source tree.
- **Versioning**: Use subfolders such as `texture/upscaled/v1`, `texture/upscaled/v2`, etc., to keep multiple passes. Update the manifest to point to the current version.
- **Integrity**: Include checksums (e.g., SHA256) in the manifest to verify downstream syncs.

## Automating Sync to a Separate Folder and GitHub
To keep outputs organized and backed up, automate synchronization after each pipeline run:

### Local/Cluster Folder Sync
```bash
# Sync the latest upscaled outputs to a staging folder (e.g., /data/HDtextureDDS-sync)
rsync -avh --delete texture/upscaled/ /data/HDtextureDDS-sync/upscaled/
rsync -avh --delete texture/manifests/ /data/HDtextureDDS-sync/manifests/
```

### GitHub Sync
```bash
# From the repository root with outputs already staged under texture/upscaled
cd /path/to/HDtextureDDS
# Optionally copy staged artifacts into the repo (or use git-lfs for large binaries)
rsync -avh /data/HDtextureDDS-sync/upscaled/ texture/upscaled/
rsync -avh /data/HDtextureDDS-sync/manifests/ texture/manifests/

git add texture/upscaled texture/manifests
git commit -m "chore: sync latest upscaled textures"
git push origin main
```

### Automation Tips
- Wrap the pipeline + sync steps in a cron job or systemd timer on the cluster login node.
- If using multiple compute nodes, write outputs to shared storage so only the login node performs the Git sync.
- For large binaries, configure Git LFS (`git lfs track "*.dds"`) to avoid repository bloat.

## Applying Upscaled Textures In-Game
Once the upscaled outputs are available:
1. Copy or link the contents of `texture/upscaled/` into the game/mod texture directory.
2. Clear any shader or asset cache the game maintains so new textures load immediately.
3. Launch the game and verify visual improvements; roll back to a prior versioned folder if issues are detected.
