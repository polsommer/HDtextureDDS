#!/bin/bash
#SBATCH --job-name=dds-upscale
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=slurm-dds-%j.out
#SBATCH --mail-type=END,FAIL

# Optional: set SLURM mail address with `--mail-user` when submitting:
# sbatch --mail-user="you@example.com" cluster/swg_rpi_job.sh

set -euo pipefail

module load python/3.10 || true
module load cuda/11.7 || true

if [ -n "${DDS_VENV:-}" ]; then
  source "${DDS_VENV}/bin/activate"
fi

MODEL_CMD=${DDS_MODEL_CMD:-"python -m your_upscaler --input {input} --output {output}"}
MODEL_NAME=${DDS_MODEL_NAME:-"custom-model"}
OUTPUT_DIR=${DDS_OUTPUT_DIR:-"${PWD}/output"}
OVERWRITE_FLAG=""
if [ "${DDS_OVERWRITE:-0}" != "0" ]; then
  OVERWRITE_FLAG="--overwrite"
fi

GIT_FLAGS=()
if [ "${DDS_GIT_COMMIT:-0}" != "0" ]; then
  GIT_FLAGS+=("--git-commit")
fi
if [ "${DDS_GIT_PUSH:-0}" != "0" ]; then
  GIT_FLAGS+=("--git-push")
fi

python scripts/batch_process_dds.py \
  --input texture \
  --output "${OUTPUT_DIR}" \
  --model-name "${MODEL_NAME}" \
  --model-cmd "${MODEL_CMD}" \
  ${OVERWRITE_FLAG} \
  ${GIT_FLAGS[@]:-}
