"""Perturbed supervision, relative geometry rewards, offline render preferences."""
from pathlib import Path
import argparse
import json
import math
import random
import time

import torch
from torch.nn import functional as F
from prepare import ENGINE, rows, save, sha
from policy import (STD, WARM_START, decode, forward, full_boxes, get_model, iou,
                    prepare_sample, route, summary, violations)
from optimize_layout import score

MODEL = ENGINE / "artifacts" / "models" / "Qwen3-VL-2B-Instruct-sharded"


def log_probability(actions, mean, mask):
    return (-.5 * ((actions - mean) / STD).square())[..., mask, :].mean(dim=(-2, -1))


def supervised_loss(item, mean, rng, count=4):
    expected = item["targets"].cuda()
    mask = item["mask"].cuda()
    # Perturb centers and one shared size factor to preserve the physical aspect.
    noise = torch.randn((count, *expected[:, :3].shape), generator=rng).cuda() * .008
    dims = expected[:, 2:] * (1 + noise[..., 2:] * 4)
    xy = expected[:, :2] + noise[..., :2] + (expected[:, 2:] - dims) / 2
    targets = torch.cat((xy, dims), dim=-1)
    pred = decode(item, mean)[mask]
    return F.smooth_l1_loss(pred.expand(count, -1, -1), targets[:, mask], beta=.02)


def geometry_loss(item, mean, reference, rng):
    mask = item["mask"].cuda()
    old = mean.detach()
    actions = old + torch.randn((8, *old.shape), generator=rng).cuda() * STD
    candidates = decode(item, actions)
    target = item["targets"].cuda()
    reward = iou(candidates[:, mask], target[mask]).mean(-1)
    reward -= 2 * violations(candidates.detach(), item).to(reward.device)
    advantage = (reward - reward.mean()) / reward.std(unbiased=False).clamp_min(.015)
    ratio = (log_probability(actions, mean, mask) - log_probability(actions, old, mask)).exp()
    policy = -torch.minimum(ratio * advantage, ratio.clamp(.8, 1.2) * advantage).mean()
    trust = ((mean[mask] - reference[mask]) / STD).square().mean()
    return .035 * policy + .025 * trust + supervised_loss(item, mean, rng, count=1)


def preference_loss(item, mean, reference, pair, rng):
    mask = item["mask"].cuda() & torch.tensor(pair["changed"], device="cuda")
    actions = torch.tensor([pair["preferred"], pair["rejected"]], device="cuda")
    change = log_probability(actions, mean, mask) - log_probability(actions, reference, mask)
    dpo = -F.logsigmoid(change[0] - change[1])
    return .025 * dpo + supervised_loss(item, mean, rng, count=1)


def collect(model, processor, head, items, bundle, image=True):
    model.eval()
    head.eval()
    result = []
    with torch.inference_mode():
        for item in items:
            actions = forward(model, processor, head, item, bundle, image)
            result.append(actions.cpu())
    # Inference tensors cannot safely be used in backward as reference inputs.
    return [v.clone() for v in result]


def measurements(items, actions, path):
    predicted = [(full_boxes(item, value) * 1000).tolist() for item, value in zip(items, actions)]
    info = summary(items, predicted)
    cases = score(items, predicted)["cases"]
    for item, case in zip(items, cases):
        case["route"] = route(item)
    save(path, {**info, "cases": cases})
    return info, cases


def snapshot(model, head, folder):
    folder.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(folder)
    torch.save(head.state_dict(), folder / "numeric-head.pt")


def restore(model, head, folder):
    from peft import set_peft_model_state_dict
    from safetensors.torch import load_file
    set_peft_model_state_dict(model, load_file(str(folder / "adapter_model.safetensors")))
    head.load_state_dict(torch.load(folder / "numeric-head.pt", weights_only=True))


def train(args):
    data = [prepare_sample(row, args.bundle, True) for row in rows(args.bundle / "samples.jsonl")]
    train_set = [item for item in data if item["sample"]["split"] == "train" and item["mask"].any()]
    splits = {key: [i for i in data if i["sample"]["split"] == key]
              for key in ("validation", "test")}
    if not train_set or any(not items for items in splits.values()):
        raise ValueError("train, validation and test must be nonempty")
    args.run.mkdir(parents=True, exist_ok=False)
    save(args.run / "configuration.json", {"arguments": {k: str(v) for k, v in vars(args).items()},
         "samplesSha256": sha(args.bundle / "samples.jsonl"),
         "model": str(args.model.resolve()), "imagePixels": 100352,
         "promptTokens": 4096, "policyStd": STD,
         "trainScenes": len(train_set), "nativeValidation": "not_measured"})
    torch.manual_seed(args.seed)
    randomizer = random.Random(args.seed)
    rng = torch.Generator().manual_seed(args.seed)
    model, processor, head = get_model(args.model, args.warm_start, args.rank)
    start = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    trainable = [p for p in model.parameters() if p.requires_grad]
    lora_before = [p.detach().cpu().clone() for p in trainable]
    parameters = trainable + list(head.parameters())
    optimizer = torch.optim.AdamW([{"params": trainable, "lr": args.lr},
                                   {"params": head.parameters(), "lr": args.lr * 5}])
    baseline_path = args.run / "checkpoints" / "initial"
    snapshot(model, head, baseline_path)
    valid_set = splits["validation"]
    history = {}
    best_score = float("inf")
    best_phase = "initial"
    step_count = 0
    for phase in ("initial", "psft", "geometry", "preference"):
        if phase != "initial":
            if phase == "preference":
                pairs = {row["id"]: row for row in rows(args.preferences)}
                chosen = [item for item in train_set if item["sample"]["id"] in pairs]
                if not chosen:
                    raise ValueError("No valid training image preference pairs")
            else:
                chosen = train_set.copy()
            repeats = args.psft_epochs if phase == "psft" else 1
            references = collect(model, processor, head, chosen, args.bundle) if phase != "psft" else []
            optimizer.state.clear()
            model.train()
            head.train()
            for epoch in range(repeats):
                order = list(range(len(chosen)))
                randomizer.shuffle(order)
                for start_index in range(0, len(order), args.accumulation):
                    batch_indices = order[start_index:start_index + args.accumulation]
                    optimizer.zero_grad(set_to_none=True)
                    losses = []
                    for index in batch_indices:
                        item = chosen[index]
                        mean = forward(model, processor, head, item, args.bundle)
                        if phase == "psft":
                            loss = supervised_loss(item, mean, rng)
                        elif phase == "geometry":
                            loss = geometry_loss(item, mean, references[index].cuda(), rng)
                        else:
                            pair = pairs[item["sample"]["id"]]
                            loss = preference_loss(item, mean, references[index].cuda(), pair, rng)
                        if not math.isfinite(float(loss.detach())):
                            raise RuntimeError(f"Nonfinite {phase} loss")
                        (loss / len(batch_indices)).backward()
                        losses.append(float(loss.detach()))
                    torch.nn.utils.clip_grad_norm_(parameters, 1.0, error_if_nonfinite=True)
                    optimizer.step()
                    step_count += 1
                    if step_count % 5 == 0:
                        print(json.dumps({"phase": phase, "epoch": epoch + 1, "step": step_count,
                                          "examples": start_index + len(losses), "total": len(order),
                                          "loss": sum(losses) / len(losses)}), flush=True)
            snapshot(model, head, args.run / "checkpoints" / phase)
        values = collect(model, processor, head, valid_set, args.bundle)
        stats, _ = measurements(valid_set, values, args.run / f"validation-{phase}.json")
        history[phase] = {**stats, "optimizerSteps": step_count}
        selection = stats["teacherMAE1000"] + 100 * stats["safetyProxy"]
        if selection < best_score:
            best_score, best_phase = selection, phase
        save(args.run / "validation-history.json", history)
        print(json.dumps({"phase": phase, "validation": stats, "selected": best_phase}), flush=True)
    changed = sum(not torch.equal(a, b.detach().cpu()) for a, b in zip(lora_before, trainable))
    if not changed:
        raise RuntimeError("LoRA parameters did not change")
    save(args.run / "selection.json", {"phase": best_phase, "testUsed": False,
         "metric": "validation teacher MAE1000 + 100 * safety proxy",
         "selectedScore": best_score})
    test = splits["test"]
    prior = [torch.zeros_like(item["constants"][:, :3]) for item in test]
    comparison = {"contain": measurements(test, prior, args.run / "test-contain.json")[0]}
    for phase in dict.fromkeys(("initial", "preference", best_phase)):
        restore(model, head, args.run / "checkpoints" / phase)
        result = collect(model, processor, head, test, args.bundle)
        comparison[phase], cases = measurements(test, result, args.run / f"test-{phase}.json")
        if phase == best_phase:
            snapshot(model, head, args.run / "adapter")
            save(args.run / "test-predictions.json", {
                item["sample"]["requestKey"]: {"answer": case["answer"], "provider": "harmony-poster"}
                for item, case in zip(test, cases)})
    restore(model, head, args.run / "adapter")
    blank = collect(model, processor, head, valid_set, args.bundle, image=False)
    comparison["validationBlankImage"], _ = measurements(valid_set, blank, args.run / "validation-blank.json")
    save(args.run / "result.json", {"status": "complete", "selected": best_phase,
         "optimizerSteps": step_count, "changedLoraTensors": changed, "test": comparison,
         "peakAllocatedGiB": torch.cuda.max_memory_allocated() / 2**30,
         "seconds": round(time.monotonic() - start, 2),
         "adapterSha256": sha(args.run / "adapter" / "adapter_model.safetensors"),
         "headSha256": sha(args.run / "adapter" / "numeric-head.pt")})
    print(json.dumps(comparison), flush=True)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--bundle", type=Path, required=True)
    cli.add_argument("--preferences", type=Path, required=True)
    cli.add_argument("--model", type=Path, default=MODEL)
    initial = cli.add_mutually_exclusive_group()
    initial.add_argument("--warm-start", type=Path, default=WARM_START)
    initial.add_argument("--from-base", dest="warm_start", action="store_const", const=None)
    cli.add_argument("--run", type=Path, required=True)
    cli.add_argument("--rank", type=int, default=8)
    cli.add_argument("--seed", type=int, default=31)
    cli.add_argument("--accumulation", type=int, default=4)
    cli.add_argument("--psft-epochs", type=int, default=1)
    cli.add_argument("--lr", type=float, default=8e-6)
    args = cli.parse_args()
    if args.accumulation < 1 or args.psft_epochs < 1:
        cli.error("accumulation and PSFT epochs must be positive")
    train(args)


if __name__ == "__main__":
    main()
