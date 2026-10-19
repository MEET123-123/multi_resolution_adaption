#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root after installing requirements-ml.txt in a CUDA
# Python environment. SFT files already contain images + JSON; no XML/HWT reads.
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen3-VL-8B-Instruct}"
SFT_PATH="${SFT_PATH:-artifacts/learning/sft-reviewed}"
ADAPTER_PATH="${ADAPTER_PATH:-artifacts/learning/adapters/server-run-1}"
GPU_COUNT="${GPU_COUNT:-2}"
torchrun --standalone --nproc_per_node="$GPU_COUNT" \
  --module multires_app.learning.qwen_runner train \
  --model "$MODEL_PATH" --sft "$SFT_PATH" --out "$ADAPTER_PATH" \
  --max-steps 100 --rank 16 --gradient-accumulation 8 \
  --max-pixels 401408 --max-length 8192 --learning-rate 0.0001 "$@"
