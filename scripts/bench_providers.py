#!/usr/bin/env python3
"""Latency and token cost per role and per real operation, measured against live providers.

This measures the operations the product actually performs, through their real code
paths — not a synthetic ping:

- ``route``  (policy) — decide browser loop vs tool orchestrator for a mission.
- ``choose`` (policy) — the hot path: pick the next action on an observed page.
- ``plan``   (planner) — decompose a mission into an ordered checklist.
- ``text``   (text) — produce the value for one typed field.

Each sample reports wall-clock latency for the whole operation plus the tokens the
provider billed, so a slow-but-cheap model and a fast-but-expensive one are
comparable on the same table. Costs are only printed when you supply prices
(``--prices prices.json``), because an invented price is worse than no price:

    {"nvidia:z-ai/glm-5.3-flash": {"input": 0.0, "output": 0.0},
     "nvidia:z-ai/glm-5.3": {"input": 0.0, "output": 0.0},
     "groq:openai/gpt-oss-20b": {"input": 0.15, "output": 0.75}}

Usage:
    python scripts/bench_providers.py --samples 3
    python scripts/bench_providers.py --operations choose,route --json artifacts/providers.json
"""

import argparse
import json
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROUTING_SYSTEM = (
    "You are a mission router for a browser agent. Reply with ONLY one JSON object: "
    '{"choice": "browser"} or {"choice": "orchestrated"}.'
)

MISSION = "Find one-way flights from Zurich to London on September 20, 2026 and stop when results are visible."

# A realistic observed page: the shapes the policy model sees on every step of the
# real flights demo, with enough elements to make the choice non-trivial.
PAGE = {
    "url": "https://www.google.com/travel/flights?hl=en",
    "title": "Flights",
    "text": (
        "Flights  One way  Round trip  Where from?  Where to?  Departure  Return  "
        "Passengers  1 adult  Economy  Search  Change currency  Sort by price"
    ),
    "scroll": {"y": 0},
    "actions": [
        {"id": "e1", "kind": "fill", "label": "Where from?", "role": "combobox", "value": "", "node": 10},
        {"id": "e2", "kind": "fill", "label": "Where to?", "role": "combobox", "value": "", "node": 11},
        {"id": "e3", "kind": "fill", "label": "Departure", "role": "textbox", "value": "", "node": 12},
        {"id": "e4", "kind": "click", "label": "One way", "role": "radio", "value": "", "node": 13},
        {"id": "e5", "kind": "click", "label": "Passengers", "role": "button", "value": "", "node": 14},
        {"id": "e6", "kind": "click", "label": "Search", "role": "button", "value": "", "node": 15},
        {"id": "e7", "kind": "click", "label": "Change currency", "role": "button", "value": "", "node": 16},
        {"id": "e8", "kind": "click", "label": "Sort by price", "role": "button", "value": "", "node": 17},
        {"id": "e9", "kind": "select", "label": "Cabin class", "role": "combobox", "value": "Economy", "node": 18},
        {"id": "done", "kind": "done", "label": "DONE"},
        {"id": "wait", "kind": "wait", "label": "Wait"},
    ],
}
PAGE["fingerprint"] = "benchmark"


def _percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(quantile * (len(ordered) - 1))))
    return ordered[index]


@contextmanager
def recording_chat():
    """Wrap providers.chat to capture the usage of every call an operation makes."""
    from jev_ultrafast import providers

    seen = {"usage": [], "calls": 0, "models": []}
    original = providers.chat

    def spy(provider, system, user, max_tokens=1024, on_delta=None):
        content, meta = original(provider, system, user, max_tokens=max_tokens, on_delta=on_delta)
        seen["calls"] += 1
        seen["usage"].append(meta.get("usage") or {})
        seen["models"].append(meta.get("model") or provider["model"])
        return content, meta

    providers.chat = spy
    try:
        yield seen
    finally:
        providers.chat = original


def _usage_totals(samples):
    fields_in = ("prompt_tokens", "input_tokens")
    fields_out = ("completion_tokens", "output_tokens")
    tokens_in = sum(next((u.get(f) or 0 for f in fields_in if u.get(f)), 0) for u in samples)
    tokens_out = sum(next((u.get(f) or 0 for f in fields_out if u.get(f)), 0) for u in samples)
    return tokens_in, tokens_out


def build_operation(name):
    """The callable for one operation, bound to whichever role it needs."""
    from jev_ultrafast import model, providers

    if name == "route":
        provider = providers.resolve("policy")

        def route():
            content, _meta = providers.chat(provider, ROUTING_SYSTEM, f"MISSION: {MISSION}", max_tokens=200)
            return providers.extract_json(content).get("choice")

        return {"role": "policy", "action": route}

    if name == "choose":
        providers.resolve("policy")  # fail early when the role is unconfigured

        def choose():
            return model.choose(PAGE, MISSION, [])["choice"]

        return {"role": "policy", "action": choose}

    if name == "plan":
        providers.resolve("planner")

        def plan():
            return model.plan_steps(MISSION, PAGE)

        return {"role": "planner", "action": plan}

    if name == "text":
        providers.resolve("text")
        context = model.field_context(MISSION, PAGE["actions"][0], PAGE, [])

        def text():
            return model.field_text(context)[0]

        return {"role": "text", "action": text}

    raise ValueError(f"unknown operation {name!r}")


def measure_operation(name, operation, samples, pacing):
    """Run one operation `samples` times; a failure is recorded, never hidden."""
    entry = {
        "operation": name,
        "role": operation["role"],
        "samples": samples,
        "ok": 0,
        "latency_ms": [],
        "tokens_in": 0,
        "tokens_out": 0,
        "error": None,
        "values": [],
    }
    for index in range(samples):
        if index:
            time.sleep(pacing)
        started = time.perf_counter()
        try:
            with recording_chat() as seen:
                value = operation["action"]()
        except (ValueError, RuntimeError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"[:300]
            continue
        entry["latency_ms"].append(round((time.perf_counter() - started) * 1000, 1))
        entry["values"].append(str(value)[:60])
        entry["ok"] += 1
        tokens_in, tokens_out = _usage_totals(seen["usage"])
        entry["tokens_in"] += tokens_in
        entry["tokens_out"] += tokens_out
    return entry


def load_prices(path):
    """{model: {input, output}} in USD per million tokens, or an empty map."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prices = {}
    for model, entry in data.items():
        if not isinstance(entry, dict) or "input" not in entry or "output" not in entry:
            raise ValueError(f"prices for {model} need input and output (USD per 1M tokens)")
        prices[model] = {"input": float(entry["input"]), "output": float(entry["output"])}
    return prices


def price_for(prices, role_model):
    """Exact key first, then the bare model name."""
    if role_model in prices:
        return prices[role_model]
    bare = role_model.split(":", 1)[-1]
    return prices.get(bare)


def cost_usd(entry, price, calls):
    if not price or not entry["ok"]:
        return None
    scale = calls / entry["ok"]
    return (entry["tokens_in"] * scale / 1e6) * price["input"] + (entry["tokens_out"] * scale / 1e6) * price["output"]


def render_report(results, prices, per_thousand=True):
    lines = ["## ⏱️ Provider bench — latency and tokens per operation", ""]
    lines += [
        "| Role | Operation | OK | min | p50 | p95 | max | tokens in | tokens out |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in results:
        latency = entry["latency_ms"]
        cells = [
            entry["role"],
            f"`{entry['operation']}`",
            f"{entry['ok']}/{entry['samples']}",
            f"{min(latency):.0f} ms" if latency else "—",
            f"{statistics.median(latency):.0f} ms" if latency else "—",
            f"{_percentile(latency, 0.95):.0f} ms" if latency else "—",
            f"{max(latency):.0f} ms" if latency else "—",
            f"{entry['tokens_in']:,}" if entry["ok"] else "—",
            f"{entry['tokens_out']:,}" if entry["ok"] else "—",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    for entry in results:
        if entry["error"]:
            lines += [f"- `{entry['operation']}` failed: {entry['error']}", ""]

    if prices:
        calls = 1000 if per_thousand else 1
        lines += [f"### Projected cost per {calls:,} calls", "",
                  "| Operation | Model | Cost |", "|---|---|---|"]
        for entry in results:
            model_name = entry.get("model") or "—"
            price = price_for(prices, model_name)
            estimate = cost_usd(entry, price, calls)
            rendered = f"${estimate:.4f}" if estimate is not None else "no price supplied"
            lines.append(f"| `{entry['operation']}` | `{model_name}` | {rendered} |")
        lines.append("")
    else:
        lines += ["Costs omitted: pass `--prices prices.json` (USD per million tokens) to project them.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--operations", default="route,choose,plan,text")
    parser.add_argument("--pacing", type=float, default=0.5, help="seconds between calls (free-tier friendly)")
    parser.add_argument("--prices", default="")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    from jev_ultrafast import providers

    results, skipped = [], []
    for name in [item.strip() for item in args.operations.split(",") if item.strip()]:
        try:
            operation = build_operation(name)
        except ValueError as error:
            skipped.append((name, str(error)[:200]))
            continue
        entry = measure_operation(name, operation, args.samples, args.pacing)
        try:
            entry["model"] = f"{providers.resolve(entry['role'])['name']}:{providers.resolve(entry['role'])['model']}"
        except ValueError:
            entry["model"] = None
        results.append(entry)

    prices = load_prices(args.prices) if args.prices else {}
    print(render_report(results, prices))
    if skipped:
        print("\nSkipped operations (role not configured):")
        for name, reason in skipped:
            print(f"- `{name}`: {reason}")

    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "results": results,
            "prices": prices,
            "skipped": [{"operation": name, "reason": reason} for name, reason in skipped],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON written to {destination}")

    if results and not any(entry["ok"] for entry in results):
        print("\n🔴 No operation completed — check the provider configuration.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
