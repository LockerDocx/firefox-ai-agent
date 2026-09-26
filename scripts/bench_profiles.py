#!/usr/bin/env python3
"""Measure candidate model profiles: does "limit the AI" actually buy speed, and at what cost?

The question this answers, in the user's words: *use glm-5.3 flash without reasoning* to get a
faster agent on one free key. The honest answer needs two numbers per candidate, both measured
against the live endpoints and both taken from the calls the agent really makes:

- **speed** — wall-clock latency per role, from the app's own prompts (median and worst).
- **quality** — is the answer still usable? Three objective checks, one per role:
    * planner: a valid plan (JSON, 1..MAX_PLAN_STEPS concrete steps, no prose around it),
    * executor/policy: the routing decision on a stratified sample of the project's own battery
      of 241 labelled missions, where *dangerous confusion* (a mission that needs tools routed
      to the browser loop) is counted separately because that failure cannot self-heal,
    * text writer: does it return the exact value the goal supplied, as the field-filling
      contract demands, instead of inventing or wrapping it.

This is not an intelligence benchmark. A model can be fast, contract-clean and still plan worse
than another; what it cannot do is pass these checks while being broken. Read the numbers as
"this candidate is not worse at the jobs the agent gives it".

Usage:
    python scripts/bench_profiles.py --list
    python scripts/bench_profiles.py --profile nvidia-flash-none
    python scripts/bench_profiles.py --profile nvidia-flash-none --routing-sample 12 --plans 3

Exit code is 0 for a measurement (it prints a report whatever happens); pass --floor 0.75 to
fail below a routing accuracy.
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jev_ultrafast import discovery, providers, schemas  # noqa: E402
from jev_ultrafast.firefox import load_environment  # noqa: E402
from jev_ultrafast.parameters import ROLE_MODEL_ENV, ROLE_PARAM_ENV  # noqa: E402
from jev_ultrafast.questions import PLANNER_SYSTEM, TEXT_VALUE  # noqa: E402
from scripts.bench_routing import ROUTING_SYSTEM  # noqa: E402
from scripts.routing_cases import ROUTING_CASES  # noqa: E402

# ── the candidates ───────────────────────────────────────────────────────────
# Every profile says, per role: which provider, which model, and how much reasoning to allow.
# "none" is not "a bit less thinking": for GLM it is chat_template_kwargs.thinking=false, the
# model answers straight away (schemas.reasoning_body). "default" leaves the setting alone.
PROFILES = {
    "nvidia-current": {
        "note": "what one NVIDIA key derives today: glm-5.3-flash everywhere, thinking off",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3-flash", "none"),
            "policy": ("nvidia", "z-ai/glm-5.3-flash", "none"),
            "text": ("nvidia", "z-ai/glm-5.3-flash", "none"),
        },
    },
    "nvidia-big-none": {
        "note": "the same setup on the full-size model: the comparison that decided the default",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3", "none"),
            "policy": ("nvidia", "z-ai/glm-5.3", "none"),
            "text": ("nvidia", "z-ai/glm-5.3", "none"),
        },
    },
    "nvidia-flash-none": {
        "note": "the proposal: glm-5.3 flash everywhere, thinking off",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3-flash", "none"),
            "policy": ("nvidia", "z-ai/glm-5.3-flash", "none"),
            "text": ("nvidia", "z-ai/glm-5.3-flash", "none"),
        },
    },
    "nvidia-flash-low": {
        "note": "flash with the cheapest thinking on (does the thinking pay for itself?)",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3-flash", "low"),
            "policy": ("nvidia", "z-ai/glm-5.3-flash", "low"),
            "text": ("nvidia", "z-ai/glm-5.3-flash", "low"),
        },
    },
    "nvidia-glm-none": {
        "note": "the bigger model with thinking off: same idea, more parameters",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3", "none"),
            "policy": ("nvidia", "z-ai/glm-5.3", "none"),
            "text": ("nvidia", "z-ai/glm-5.3", "none"),
        },
    },
    "nvidia-hybrid": {
        "note": "big model plans, flash executes without thinking",
        "roles": {
            "planner": ("nvidia", "z-ai/glm-5.3", "default"),
            "policy": ("nvidia", "z-ai/glm-5.3-flash", "none"),
            "text": ("nvidia", "z-ai/glm-5.3-flash", "none"),
        },
    },
    "groq-reference": {
        "note": "the fast anchor, for scale: all three roles on free Groq",
        "roles": {
            "planner": ("groq", "openai/gpt-oss-120b", "default"),
            "policy": ("groq", "openai/gpt-oss-20b", "default"),
            "text": ("groq", "openai/gpt-oss-20b", "default"),
        },
    },
}

# The token budgets are the app's own (model.py: planner 1024, policy and text 2048), not
# something the bench chose: with a smaller budget a long plan comes back cut in half and the
# report would blame the model for the bench's truncation. Measured, and it happened.
BUDGETS = {"planner": 1024, "policy": 2048, "text": 2048}

PLANNER_MISSIONS = (
    "Book the cheapest direct flight from Barcelona to Rome next Friday, one adult",
    "Find the schedule of the Sagrada Familia and tell me if it opens on Sunday morning",
    "On the page I am looking at, order the results by price from low to high",
    "Reserve a table for four people on Friday at 21:00 in a restaurant near the Gothic Quarter",
    "Find a hotel in Lisbon for two nights from 12 October, with breakfast, and open the cheapest",
    "Compare the price of the iPhone on three shops and tell me which one is cheapest today",
    "Fill the contact form with my details and attach my CV from the Downloads folder",
    "Download the invoice for August from my bank account page",
    "Summarise the ten reviews on this page and tell me what people complain about",
    "Change the delivery address on the order I am looking at and confirm it",
)

TEXT_CASES = (
    # goal, field, the exact value the goal supplies (what the contract asks for)
    ("Reserve a table for 4 people in Barcelona on 12 October", "Number of people", "4"),
    ("Send the invoice to maria.lopez@example.com", "Email address", "maria.lopez@example.com"),
    ("Fly from Barcelona to Rome on 2026-10-12, one adult", "Departure city", "Barcelona"),
    ("Book a room for 2026-10-12, two nights, one guest", "Check-in date", "2026-10-12"),
)


def apply_profile(profile):
    """Put one candidate's provider/model/reasoning for every role into the environment.

    The variable names come from the app itself (parameters.ROLE_MODEL_ENV), not from string
    building: the text role is TEXT_MODEL_PROVIDER + TEXT_MODEL, and a bench that guessed
    `TEXT_MODEL_MODEL` would quietly measure the default model instead of the candidate.
    """
    for role, (provider_name, model, reasoning) in profile["roles"].items():
        provider_var, model_var = ROLE_MODEL_ENV[role]
        os.environ[provider_var] = provider_name
        os.environ[model_var] = model
        reasoning_var = ROLE_PARAM_ENV[(role, "reasoning")]
        if reasoning == "default":
            os.environ.pop(reasoning_var, None)
        else:
            os.environ[reasoning_var] = reasoning


def describe_wire(role):
    """The provider config as the agent will send it: proof of what actually goes out."""
    try:
        provider = providers.resolve(role)
    except ValueError as error:
        return {"error": str(error)}
    return {
        "model": f"{provider['name']}:{provider['model']}",
        "reasoning": provider.get("reasoning"),
        "params": dict(provider.get("params") or {}),
        "schemaId": (provider.get("schema") or {}).get("schemaId"),
    }


def catalogue(provider_name):
    """The provider's own model list, so 'does this model exist?' is answered by the provider.

    Returns (ids, unavailable_reason): without a key the list cannot be fetched, and the report
    must say that instead of claiming the model is missing.
    """
    try:
        return [model["id"] for model in discovery.fetch_models(provider_name)], None
    except Exception as error:  # noqa: BLE001 - a missing catalogue is a finding, not a crash
        return [], str(error)[:140]


def call(role, system, user, max_tokens):
    """One real request for a role, with its latency and its failure recorded the same way.

    A missing key is a finding too (a profile that needs NVIDIA cannot be measured with only a
    Groq key): it is reported as that role's failure instead of ending the run.
    """
    started = time.perf_counter()
    try:
        provider = providers.resolve(role)
        content, meta = providers.chat(provider, system, user, max_tokens=max_tokens)
    except (RuntimeError, ValueError) as error:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": str(error)[:200],
            "content": None,
        }
    return {
        "ok": True,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "error": None,
        "content": content,
        "usage": (meta or {}).get("usage") or {},
    }


def stats(latencies):
    if not latencies:
        return {"n": 0, "median_ms": None, "worst_ms": None}
    return {
        "n": len(latencies),
        "median_ms": int(statistics.median(latencies)),
        "worst_ms": max(latencies),
    }


def run_profile(name, profile, args):
    apply_profile(profile)
    report = {"profile": name, "note": profile["note"], "roles": {}, "wire": {}, "quality": {},
              "budgets": dict(BUDGETS)}

    for role in ("planner", "policy", "text"):
        report["wire"][role] = describe_wire(role)

    # ── the model ids, against the provider's own catalogue ──────────────────
    report["catalogue"] = {}
    for provider_name in sorted({role[0] for role in profile["roles"].values()}):
        ids, unavailable = catalogue(provider_name)
        wanted = sorted({role[1] for role in profile["roles"].values() if role[0] == provider_name})
        with_glm = [model for model in ids if "glm" in model] or [
            model for model in ids if "gpt-oss" in model
        ]
        report["catalogue"][provider_name] = {
            "unavailable": unavailable,
            "wanted": {model: model in ids for model in wanted},
            "matching": with_glm[:14],
        }

    # ── 1. the planner: a valid plan, and how long it took ───────────────────
    plans, planner_latencies, planner_failures = [], [], []
    for mission in PLANNER_MISSIONS[: args.plans]:
        result = call("planner", PLANNER_SYSTEM, f"MISSION: {mission}", BUDGETS["planner"])
        if not result["ok"]:
            planner_failures.append(result["error"])
            continue
        planner_latencies.append(result["latency_ms"])
        try:
            parsed = providers.extract_json(result["content"])
            steps = parsed.get("steps")
            valid = isinstance(steps, list) and 1 <= len(steps) <= 12 and all(
                isinstance(step, str) and step.strip() for step in steps
            )
        except ValueError as error:
            valid, steps, parsed = False, None, {"error": str(error)[:80]}
        plans.append(
            {
                "mission": mission,
                "valid": valid,
                "steps": steps,
                "raw": (result["content"] or "")[:400],
                # A failed plan is worth the whole reply: a truncated JSON and a wrong shape look
                # the same in a preview, and only one of them is the model's fault.
                "raw_full": None if valid else (result["content"] or ""),
            }
        )
    report["quality"]["planner"] = {
        "valid": sum(1 for plan in plans if plan["valid"]),
        "total": len(plans),
        "failures": planner_failures,
        "latency": stats(planner_latencies),
        "plans": plans,
    }

    # ── 2. the executor: the routing decision on the project's own battery ───
    stride = max(1, len(ROUTING_CASES) // max(1, args.routing_sample))
    cases = ROUTING_CASES[::stride][: args.routing_sample]
    routing_rows, policy_latencies, policy_failures = [], [], []
    dangerous = 0
    for case in cases:
        result = call("policy", ROUTING_SYSTEM, f"MISSION: {case['mission']}", BUDGETS["policy"])
        if not result["ok"]:
            policy_failures.append(result["error"])
            continue
        policy_latencies.append(result["latency_ms"])
        try:
            answer = providers.extract_json(result["content"]).get("choice")
        except ValueError:
            answer = None
        hit = answer == case["expected"]
        if case["expected"] == "orchestrated" and answer == "browser":
            dangerous += 1
        routing_rows.append(
            {"mission": case["mission"], "expected": case["expected"], "answer": answer, "hit": hit}
        )
    scored = [row for row in routing_rows if row["answer"]]
    report["quality"]["policy"] = {
        "hits": sum(1 for row in scored if row["hit"]),
        "scored": len(scored),
        "answers": len(routing_rows),
        "unparseable": len(routing_rows) - len(scored),
        "dangerous_confusion": dangerous,
        "failures": policy_failures,
        "latency": stats(policy_latencies),
        "rows": routing_rows,
    }

    # ── 3. the text writer: the value the goal supplied, exactly ─────────────
    text_rows, text_latencies, text_failures = [], [], []
    for goal, field, expected in TEXT_CASES[: args.text_cases]:
        result = call("text", TEXT_VALUE, f"GOAL: {goal}\nFIELD: {field}", BUDGETS["text"])
        if not result["ok"]:
            text_failures.append(result["error"])
            continue
        text_latencies.append(result["latency_ms"])
        try:
            answer = providers.extract_json(result["content"]).get("text")
        except ValueError:
            answer = None
        text_rows.append({"field": field, "expected": expected, "answer": answer, "hit": answer == expected})
    report["quality"]["text"] = {
        "exact": sum(1 for row in text_rows if row["hit"]),
        "total": len(text_rows),
        "failures": text_failures,
        "latency": stats(text_latencies),
        "rows": text_rows,
    }

    # ── what the endpoints themselves accepted, for the models this profile used ──
    report["runtime_evidence"] = {}
    for role, wire in report["wire"].items():
        if wire.get("error"):
            continue
        provider_name, _, model_id = wire["model"].partition(":")
        report["runtime_evidence"][wire["model"]] = schemas.runtime_evidence(provider_name, model_id)
    return report


def render(report):
    print(f"\n## Profile: `{report['profile']}`\n")
    print(f"*{report['note']}*\n")
    print(f"- token budget per role: {report.get('budgets')}\n")
    print("| Role | Provider:model | reasoning sent | wire params |")
    print("| --- | --- | --- | --- |")
    for role, wire in report["wire"].items():
        if wire.get("error"):
            print(f"| {role} | — | — | {wire['error'][:60]} |")
            continue
        params = ", ".join(f"{k}={v}" for k, v in wire["params"].items()) or "none"
        print(f"| {role} | `{wire['model']}` | `{wire['reasoning']}` | {params} |")
    print()
    for provider_name, info in report["catalogue"].items():
        if info["unavailable"]:
            print(f"- catalogue `{provider_name}` unavailable: {info['unavailable']}")
            continue
        missing = [model for model, present in info["wanted"].items() if not present]
        print(
            f"- catalogue `{provider_name}`: "
            + ("every wanted model exists" if not missing else f"**missing: {missing}**")
        )
        if info["matching"]:
            print(f"  - nearby ids the provider offers: {', '.join('`' + m + '`' for m in info['matching'])}")
    print()

    planner = report["quality"]["planner"]
    policy = report["quality"]["policy"]
    text = report["quality"]["text"]
    print("| Role | Median | Worst | Calls | Result |")
    print("| --- | --- | --- | --- | --- |")
    print(
        f"| planner | {planner['latency']['median_ms']} ms | {planner['latency']['worst_ms']} ms | "
        f"{planner['latency']['n']} | valid plan {planner['valid']}/{planner['total']} |"
    )
    print(
        f"| executor | {policy['latency']['median_ms']} ms | {policy['latency']['worst_ms']} ms | "
        f"{policy['latency']['n']} | routing {policy['hits']}/{policy['scored']} |"
    )
    print(
        f"| text writer | {text['latency']['median_ms']} ms | {text['latency']['worst_ms']} ms | "
        f"{text['latency']['n']} | exact value {text['exact']}/{text['total']} |"
    )
    print()
    print(
        f"- dangerous confusion (tools mission routed to the browser loop): **{policy['dangerous_confusion']}**"
    )
    print(f"- unparseable routing answers: {policy['unparseable']} of {policy['answers']}")
    for role, block in report["quality"].items():
        for failure in block["failures"][:3]:
            print(f"- {role} failure: {failure}")
    for model, evidence in report.get("runtime_evidence", {}).items():
        verified = ", ".join(evidence.get("verified") or []) or "—"
        refused = ", ".join(evidence.get("unsupported") or []) or "—"
        print(f"- `{model}` — accepted in a real request: {verified} · refused: {refused}")
    for plan in planner["plans"]:
        mark = "✅" if plan["valid"] else "❌"
        print(f"- {mark} {plan['mission'][:70]}: {json.dumps(plan['steps'], ensure_ascii=False)[:160]}")
    misses = [row for row in policy["rows"] if not row["hit"]][:6]
    for row in misses:
        print(f"- ❌ routing: {row['mission'][:70]} → {row['answer']} (expected {row['expected']})")
    for row in text["rows"]:
        mark = "✅" if row["hit"] else "❌"
        print(f"- {mark} {row['field']}: got {row['answer']!r}, goal said {row['expected']!r}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=False, help="which candidate to measure (see --list)")
    parser.add_argument("--list", action="store_true", help="list the candidates and exit")
    parser.add_argument("--names", action="store_true", help="with --list: just the names, one per line")
    parser.add_argument("--routing-sample", type=int, default=12, help="routing battery cases per profile")
    parser.add_argument("--plans", type=int, default=3, help="planner missions per profile")
    parser.add_argument("--text-cases", type=int, default=4, help="field-filling cases per profile")
    parser.add_argument("--json", help="also write the raw measurements here")
    parser.add_argument(
        "--planner-budget",
        type=int,
        default=0,
        help="override the planner's max_tokens (the app asks for 1024) for one experiment",
    )
    parser.add_argument("--floor", type=float, default=0.0, help="exit non-zero below this routing accuracy")
    args = parser.parse_args()

    if args.list or not args.profile:
        if args.list and args.names:
            for name in PROFILES:
                print(name)
            return 0
        print("Profiles:\n")
        for name, profile in PROFILES.items():
            roles = ", ".join(f"{role}={spec[1]}({spec[2]})" for role, spec in profile["roles"].items())
            print(f"  {name:20} {profile['note']}\n  {'':20} {roles}\n")
        return 0
    if args.profile not in PROFILES:
        print(f"Unknown profile '{args.profile}'. Try --list.", file=sys.stderr)
        return 2

    if args.planner_budget:
        BUDGETS["planner"] = args.planner_budget

    load_environment()  # the key file, exactly as the agent reads it
    report = render(run_profile(args.profile, PROFILES[args.profile], args))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  raw measurements: {args.json}")
    policy = report["quality"]["policy"]
    if args.floor and policy["scored"] and policy["hits"] / policy["scored"] < args.floor:
        print(
            f"\nFAILED: routing accuracy {policy['hits']}/{policy['scored']} is below the floor "
            f"{args.floor:.2f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
