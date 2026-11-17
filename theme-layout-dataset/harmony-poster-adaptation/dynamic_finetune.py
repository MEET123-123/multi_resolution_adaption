"""Continue the staged layout adapter with engine-generated dynamic states."""
from pathlib import Path
from collections import Counter
import argparse
import json
import random
import time

import torch
from torch.nn import functional as F

from prepare import rows, save, sha
from policy import decode, forward, get_model, prepare_sample
from train import MODEL, collect, measurements, restore, snapshot, supervised_loss


def dynamic_fit(item, actions, rng):
    eligible = torch.tensor([g in item["dynamicGroups"] for g in item["gids"]]) & item["mask"]
    sizes = decode(item, actions)[eligible.cuda(), 2:]
    label = item["targets"][eligible, 2:].cuda()
    # A one-sided margin preserves room for the measured slot at this input value.
    deficit = F.relu(.95 * label - sizes).mean() if eligible.any() else actions.sum() * 0
    return supervised_loss(item, actions, rng) + .2 * deficit


def evaluate(model, processor, head, items, bundle, out):
    measured = {}
    for role in ("short", "long", "existing"):
        subset = [item for item in items if item["sample"].get("dynamicPreset", "existing") == role]
        if not subset:
            continue
        actions = collect(model, processor, head, subset, bundle)
        measured[role] = measurements(subset, actions, out / f"{role}.json")[0]
    return measured


def run(args):
    all_rows = list(rows(args.bundle / "samples.jsonl"))
    all_items = [prepare_sample(s, args.bundle, supervised=True, dynamic=True) for s in all_rows]
    dynamic_train = [i for i in all_items if i["sample"]["split"] == "train"
                     and i["sample"].get("dynamicPreset") and i["mask"].any() and i["dynamicGroups"]]
    valid = [i for i in all_items if i["sample"]["split"] == "validation"]
    test = [i for i in all_items if i["sample"]["split"] == "test"]
    if not dynamic_train or not all(any(i["sample"].get("dynamicPreset") for i in split) for split in (valid, test)):
        raise ValueError("Dynamic supervision is missing from a required split")
    randomizer = random.Random(43)
    original_train = [i for i in all_items if i["sample"]["split"] == "train"
                      and not i["sample"].get("dynamicPreset") and i["mask"].any()]
    train = dynamic_train + randomizer.sample(original_train, min(len(dynamic_train), len(original_train)))
    args.run.mkdir(parents=True, exist_ok=False)
    save(args.run / "config.json", {"baseAdapter": str(args.adapter.resolve()),
         "model": str(args.model.resolve()), "bundleSha256": sha(args.bundle / "samples.jsonl"),
         "dynamicTrain": len(dynamic_train), "originalTrainReplay": len(train) - len(dynamic_train),
         "perPreset": Counter(i["sample"]["dynamicPreset"] for i in dynamic_train),
         "epochs": args.epochs, "learningRate": args.lr})
    torch.manual_seed(43)
    rng = torch.Generator().manual_seed(43)
    model, processor, head = get_model(args.model, args.adapter)
    parameters = [p for p in model.parameters() if p.requires_grad]
    initial = [p.detach().cpu().clone() for p in parameters]
    optimizer = torch.optim.AdamW([{"params": parameters, "lr": args.lr},
                                   {"params": head.parameters(), "lr": 5 * args.lr}])
    start = time.monotonic()
    steps = 0
    metrics = {}
    best_score, best = float("inf"), 0
    for epoch in range(args.epochs + 1):
        checkpoint = args.run / "checkpoints" / f"epoch-{epoch}"
        if epoch:
            order = train.copy()
            randomizer.shuffle(order)
            model.train()
            head.train()
            for offset in range(0, len(order), 4):
                subset = order[offset:offset + 4]
                optimizer.zero_grad(set_to_none=True)
                for item in subset:
                    loss = dynamic_fit(item, forward(model, processor, head, item, args.bundle), rng)
                    (loss / len(subset)).backward()
                torch.nn.utils.clip_grad_norm_(parameters + list(head.parameters()), 1., error_if_nonfinite=True)
                optimizer.step()
                steps += 1
                if steps % 5 == 0:
                    print(json.dumps({"epoch": epoch, "step": steps, "loss": float(loss.detach())}), flush=True)
        snapshot(model, head, checkpoint)
        result = evaluate(model, processor, head, valid, args.bundle, checkpoint / "validation")
        # Equal weight for the two input regimes. Keep epoch 0 if adaptation does
        # not improve the held-out dynamic cases.
        score = sum(result[role]["teacherMAE1000"] + 100 * result[role]["safetyProxy"]
                    for role in ("short", "long")) / 2
        metrics[str(epoch)] = {"score": score, "validation": result}
        if score < best_score:
            best_score, best = score, epoch
        save(args.run / "history.json", metrics)
        print(json.dumps({"epoch": epoch, "validationScore": score, "selectedEpoch": best}), flush=True)
    changed = sum(not torch.equal(a, b.detach().cpu()) for a, b in zip(initial, parameters))
    if not changed:
        raise RuntimeError("No LoRA tensors changed")
    save(args.run / "selection.json", {"selectedEpoch": best, "testUsedForSelection": False})
    comparison = {}
    for epoch in dict.fromkeys((0, args.epochs, best)):
        restore(model, head, args.run / "checkpoints" / f"epoch-{epoch}")
        comparison[str(epoch)] = evaluate(model, processor, head, test, args.bundle,
                                         args.run / f"test-epoch-{epoch}")
        if epoch == best:
            snapshot(model, head, args.run / "adapter")
    save(args.run / "result.json", {"status": "complete", "selectedEpoch": best,
         "optimizerSteps": steps, "loraTensorsChanged": changed, "seconds": time.monotonic() - start,
         "test": comparison})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-5)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("use at least one epoch")
    run(args)
