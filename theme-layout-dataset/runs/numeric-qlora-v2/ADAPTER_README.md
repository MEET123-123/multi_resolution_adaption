# Numeric layout head v2

Use Qwen3-VL-2B-Instruct plus BOTH `adapter/` and `adapter/numeric-head.pt`.
Contract: `multires-numeric-head/1`. This adapter is not a chat JSON generator.
The selected package is epoch 3, chosen on all 24 validation scenes.

The lightweight alternative loads only `geometry-head.pt` and needs no Qwen/GPU.
Both emit existing `multires-layout/1` boxes, in target safe-region xywh /1000.

From an unpacked package, with a compatible CUDA environment:

```bash
python code/theme-layout-dataset/predict_numeric_layout.py \
  --bundle /data/auto-pairs-v1/server-bundle \
  --model /models/Qwen3-VL-2B-Instruct \
  --run . --split test --out /output/numeric-predictions.json
```

Add `--backend geometry` for the numerical-feature baseline. New output paths
are required. Source images and source-member evidence come from the bundle;
teacher boxes and target images are not inference inputs. Weights load with
`weights_only=True`. Non-quantized modules use the FP32 policy of QLoRA training.

Before using proposals for actual adaptation, run `guard_numeric_proposals.py`
against source caches. Use its `accepted-predictions.json` with LearnedAnchor.
Execution replay needs source caches (or caches rebuilt from the original HWT);
image-only training bundles do not replace those caches. The included accepted
map is valid only for the already checked requests.

Full 55-scene raw teacher MAE: 46.36373; geometry-only head: 46.92843;
unlearned contain/anchor: 53.10752. After execution gating: 48 model proposals,
7 original-algorithm fallbacks, all 1043 checks pass. Native quality not measured.
Blank-image validation does not establish a benefit from visual information.
See REPORT.md and checksum manifests before interpreting these weak-label scores.
