#!/usr/bin/env python3
"""Measure candidate models on a live provider, many at a time, before choosing one.

Choosing a model by its name or its card is how a default ends up at 300 seconds per test: a
model can be the newest of its family, "flash" in the id, and still be the slowest thing the
provider serves. This asks the provider instead, with the calls the agent really makes:

    planner  a real mission -> a valid plan (JSON, 1..12 concrete steps, no prose around it)
    policy   the two-way routing decision on missions from the project's own battery
    text     the exact value the goal supplied, which is the field-filling contract

and reports, per model: median and worst latency per role, whether the answers were usable, and
how often the routing decision was right. Only what was measured is printed.

Sequential on purpose: concurrent calls share the provider's rate limits, so their latencies stop
being the latencies a user would see. `--routing 0` skips the battery, `--plans 0` skips planning.

Usage:
    python scripts/probe_models.py --catalogue
    python scripts/probe_models.py --pattern gpt-oss --pattern llama-3.1-8b --limit 8
    python scripts/probe_models.py --models meta/llama-3.1-8b-instruct,openai/gpt-oss-20b
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jev_ultrafast import discovery, providers  # noqa: E402
from jev_ultrafast.firefox import load_environment  # noqa: E402
from jev_ultrafast.parameters import ROLE_MODEL_ENV, ROLE_PARAM_ENV  # noqa: E402
from jev_ultrafast.questions import PLANNER_SYSTEM, TEXT_VALUE  # noqa: E402
from scripts.bench_profiles import BUDGETS, PLANNER_MISSIONS, TEXT_CASES  # noqa: E402
from scripts.bench_routing import ROUTING_SYSTEM  # noqa: E402
from scripts.routing_cases import ROUTING_CASES  # noqa: E402

# Ids that are certainly not what a browser agent should be calling per step: image, video,
# embedding and safety models answer /models just like the chat ones, and a probe that fails on
# them would hide the models that matter.
NOT_CHAT = ("embed", "rerank", "vision-only", "guard", "safety", "reward", "parse", "codegemma-", "bge-", "clip")


def use_model(provider_name, model, reasoning):
    """Point every role at one candidate, exactly as the agent reads its configuration."""
    for role in ("planner", "policy", "text"):
        provider_var, model_var = ROLE_MODEL_ENV[role]
        os.environ[provider_var] = provider_name
        os.environ[model_var] = model
        os.environ[ROLE_PARAM_ENV[(role, "reasoning")]] = reasoning


def wire_of(role):
    try:
        provider = providers.resolve(role)
    except ValueError as error:
        return {"error": str(error)}
    return {
        "model": f"{provider['name']}:{provider['model']}",
        "reasoning": provider.get("reasoning"),
        "params": dict(provider.get("params") or {}),
    }


def ask(role, system, user, max_tokens):
    """One real request, with its latency and its failure recorded the same way."""
    started = time.perf_counter()
    try:
        provider = providers.resolve(role)
        content, meta = providers.chat(provider, system, user, max_tokens=max_tokens)
    except (RuntimeError, ValueError) as error:
        return {"ok": False, "latency_ms": round((time.perf_counter() - started) * 1000), "error": str(error)[:200]}
    return {
        "ok": True,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "content": content,
        "usage": (meta or {}).get("usage") or {},
    }


def catalogue_ids(provider_name):
    try:
        return [entry["id"] for entry in discovery.fetch_models(provider_name)], None
    except Exception as error:  # noqa: BLE001 - a missing catalogue is a finding, not a crash
        return [], str(error)[:200]


def choose(models, patterns, ids, limit):
    if models:
        return models
    if not patterns:
        return ids[:limit]
    chosen = []
    for pattern in patterns:
        needle = pattern.lower()
        for model in ids:
            if needle in model.lower() and model not in chosen and not any(bad in model.lower() for bad in NOT_CHAT):
                chosen.append(model)
    return chosen[:limit]


def probe(model, args):
    use_model(args.provider, model, args.reasoning)
    entry = {"model": model, "wire": wire_of("policy"), "planner": None, "policy": None, "text": None}

    if args.plans:
        latencies, valid, failures = [], 0, []
        for mission in PLANNER_MISSIONS[: args.plans]:
            result = ask("planner", PLANNER_SYSTEM, f"MISSION: {mission}", BUDGETS["planner"])
            if not result["ok"]:
                failures.append(result["error"])
                continue
            latencies.append(result["latency_ms"])
            try:
                steps = providers.extract_json(result["content"]).get("steps")
                valid += int(isinstance(steps, list) and 1 <= len(steps) <= 12 and all(step.strip() for step in steps))
            except (ValueError, AttributeError):
                pass
        entry["planner"] = {
            "ms": median(latencies),
            "worst_ms": max(latencies) if latencies else None,
            "valid": valid,
            "total": len(latencies),
            "failures": failures[:2],
        }

    if args.routing:
        stride = max(1, len(ROUTING_CASES) // max(1, args.routing))
        cases = ROUTING_CASES[::stride][: args.routing]
        latencies, hits, scored, dangerous, failures = [], 0, 0, 0, []
        for case in cases:
            result = ask("policy", ROUTING_SYSTEM, f"MISSION: {case['mission']}", BUDGETS["policy"])
            if not result["ok"]:
                failures.append(result["error"])
                continue
            latencies.append(result["latency_ms"])
            try:
                answer = providers.extract_json(result["content"]).get("choice")
            except ValueError:
                answer = None
            if answer:
                scored += 1
                hits += int(answer == case["expected"])
                dangerous += int(case["expected"] == "orchestrated" and answer == "browser")
        entry["policy"] = {
            "ms": median(latencies),
            "worst_ms": max(latencies) if latencies else None,
            "hits": hits,
            "scored": scored,
            "cases": len(cases),
            "dangerous": dangerous,
            "failures": failures[:2],
        }

    if args.text:
        latencies, exact, failures = [], 0, []
        for goal, field, value in TEXT_CASES[: args.text]:
            result = ask("text", TEXT_VALUE, f"GOAL: {goal}\nFIELD: {field}", BUDGETS["text"])
            if not result["ok"]:
                failures.append(result["error"])
                continue
            latencies.append(result["latency_ms"])
            try:
                exact += int(str(providers.extract_json(result["content"]).get("text", "")).strip() == value)
            except (ValueError, AttributeError):
                pass
        entry["text"] = {
            "ms": median(latencies),
            "worst_ms": max(latencies) if latencies else None,
            "exact": exact,
            "total": len(latencies),
            "failures": failures[:2],
        }
    return entry


def median(values):
    return int(statistics.median(values)) if values else None


def show(rows, patterns):
    """The table, one row per model, widest column first so a long id does not shift the rest."""
    columns = (
        (46, "model"), (8, "plan ms"), (7, "plan ok"), (10, "routing ms"), (9, "routing"), (8, "text ms"), (5, "exact"),
    )
    print("|" + "|".join(f" {title:>{width}} " for width, title in columns) + "|")
    print("|" + "|".join("-" * (width + 2) for width, _ in columns) + "|")
    for row in rows:
        planner, policy, text = row["planner"], row["policy"], row["text"]
        cells = (
            (46, row["model"]),
            (8, show_ms(planner and planner["ms"])),
            (7, show_ratio(planner, "valid", "total")),
            (10, show_ms(policy and policy["ms"])),
            (9, show_ratio(policy, "hits", "scored")),
            (8, show_ms(text and text["ms"])),
            (5, show_ratio(text, "exact", "total")),
        )
        print("|" + "|".join(f" {value:>{width}} " for width, value in cells) + "|")
    if patterns:
        print(f"\n(pattern: {', '.join(patterns)})")


def show_ms(value):
    return "—" if value is None else f"{value}"


def show_ratio(block, hit_key, total_key):
    if not block or not block.get(total_key):
        return "—"
    return f"{block[hit_key]}/{block[total_key]}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure candidate models with the agent's own calls.")
    parser.add_argument("--provider", default="nvidia")
    parser.add_argument("--models", default="", help="comma-separated ids; empty = from the catalogue")
    parser.add_argument("--pattern", action="append", default=[], help="pick catalogue ids containing this")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--reasoning", default="none", help="the setting the app ships")
    parser.add_argument("--plans", type=int, default=1)
    parser.add_argument("--routing", type=int, default=6)
    parser.add_argument("--text", type=int, default=1)
    parser.add_argument("--catalogue", action="store_true", help="print the provider's ids and stop")
    parser.add_argument("--json", default="", help="write the raw measurements here")
    args = parser.parse_args(argv)

    load_environment()
    ids, unavailable = catalogue_ids(args.provider)

    if args.catalogue:
        if unavailable:
            print(f"catalogue unavailable: {unavailable}")
            return 1
        print("\n".join(ids))
        print(f"\n{len(ids)} models")
        return 0

    models = choose([m.strip() for m in args.models.split(",") if m.strip()], args.pattern, ids, args.limit)
    if not models:
        print("no models to probe (is there a key? try --catalogue)", file=sys.stderr)
        return 1

    print(f"probing {len(models)} model(s) on {args.provider}, reasoning={args.reasoning}")
    if unavailable:
        print(f"  (catalogue unavailable: {unavailable})")
    missing = [model for model in models if model not in ids] if ids else []
    if missing:
        print(f"  (not in the catalogue: {', '.join(missing)})")

    rows = []
    for model in models:
        started = time.perf_counter()
        row = probe(model, args)
        row["wall_ms"] = round((time.perf_counter() - started) * 1000)
        rows.append(row)
        print(f"  measured {model} in {row['wall_ms']} ms", flush=True)

    ranked = sorted(rows, key=lambda row: (row["planner"] or {}).get("ms") or 10**9)
    print()
    show(ranked, args.pattern)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"provider": args.provider, "reasoning": args.reasoning, "models": rows}, indent=2),
            encoding="utf-8",
        )
        print(f"\nraw measurements: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
