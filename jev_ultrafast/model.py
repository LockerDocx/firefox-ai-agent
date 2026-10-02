"""The policy chooses via TypeSafe Jev or any configured provider; a small model writes field values."""

import json
import math
import os
import re
import time

import httpx

from . import providers
from .questions import MAX_PLAN_STEPS, NEXT_ACTION, PLANNER_SYSTEM, POLICY_SYSTEM, TARGET, TEXT_VALUE
from .redact import redact

# How long to wait for an answer before the endpoint is called dead, and how many times to
# ask. 25 s was measured as too short for the free tiers this app is built on: on 2026-09-25
# NVIDIA NIM left three 25 s attempts hanging for the planner and the executor (76 s each)
# while the same key answered a 37.8 s call for the text role in the same check — a cold
# start or a queue, not a bad key. A wrong key is answered with HTTP 401, which is reported
# as such; a stall is not. JEV_HTTP_TIMEOUT overrides the default.
DEFAULT_TIMEOUT = 60.0
ATTEMPTS = 3


def timeout_seconds():
    """Seconds to wait for one answer: JEV_HTTP_TIMEOUT if it holds a positive number."""
    raw = (os.environ.get("JEV_HTTP_TIMEOUT") or "").strip()
    if raw:
        try:
            seconds = float(raw)
        except ValueError:
            seconds = 0.0
        if seconds > 0:
            return seconds
    return DEFAULT_TIMEOUT


def build_client():
    """The one HTTP client: patient on reads, quick on connecting to something unreachable."""
    return httpx.Client(http2=True, timeout=httpx.Timeout(timeout_seconds(), connect=15.0))


class _SharedClient:
    """The module's HTTP client, built on first use and rebuilt if JEV_HTTP_TIMEOUT changes.

    Built lazily on purpose: the key file is read into the environment at startup, which can
    happen after this module is imported, and a client frozen at import time would ignore a
    JEV_HTTP_TIMEOUT that came from .env. Anything that is not one of the three private names
    is forwarded to the live client (tests replace this object wholesale).
    """

    def __init__(self):
        self._client = None
        self._seconds = None

    def _current(self):
        seconds = timeout_seconds()
        if self._client is None or self._seconds != seconds:
            self._client = build_client()
            self._seconds = seconds
        return self._client

    def __getattr__(self, name):
        return getattr(self._current(), name)


CLIENT = _SharedClient()


# A rate limit is not a broken key and not a slow endpoint: the provider is telling us to come
# back later, and it usually says how much later. Until this was honoured, a run died on
# "Please try again in 1.319999999s" because the retries were 0.5 s and 1 s apart — the agent
# gave up a third of a second before the provider would have answered.
RATE_LIMIT_STATUS = {429, 529, 503}
RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_WAIT_CAP = 30.0  # never make the user wait longer than this without asking
_WAIT_SAID = r"(?:try again|retry|retry_after|available again)[^0-9]{0,24}"
_AMOUNT = r"([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|min|minutes?)"
_RETRY_HINT = re.compile(_WAIT_SAID + _AMOUNT, re.IGNORECASE)


def suggested_wait(response, attempt=0):
    """How long the provider itself asked us to wait, or None if it did not say.

    Reads `Retry-After` first (the standard header) and then the provider's own message, which
    is where Groq puts it ("Please try again in 1.32s"). Capped: a cap is a promise that the
    agent will not sit silent for minutes; the cap is reported in the message when it is hit.
    """
    headers = getattr(response, "headers", None)
    raw = ""
    try:
        raw = (headers or {}).get("retry-after") or ""
    except Exception:  # noqa: BLE001 - a stub response in a test has no headers
        raw = ""
    if str(raw).strip():
        try:
            return min(float(str(raw).strip()), RATE_LIMIT_WAIT_CAP)
        except ValueError:
            pass
    text = ""
    try:
        text = response.text or ""
    except Exception:  # noqa: BLE001 - same: a stub may not have .text
        text = ""
    match = _RETRY_HINT.search(text[:500])
    if not match:
        return None
    amount, unit = float(match.group(1)), match.group(2).lower()
    if unit.startswith("ms"):
        seconds = amount / 1000.0
    elif unit.startswith("m"):
        seconds = amount * 60.0
    else:
        seconds = amount
    return min(seconds + 0.25, RATE_LIMIT_WAIT_CAP)  # a hair of slack: clocks are not in step


def rate_limit_error(response, waited):
    """The message a rate limit deserves: capacity, not a bad key, and what to do about it."""
    detail = redact(" ".join((response.text or "").split()))[:300]
    return RuntimeError(
        f"Model provider rate limit (HTTP {response.status_code}) — this is capacity, not a key "
        f"problem: the free tier is spent for now and the agent waited {waited:.1f} s in total. "
        f"What to do: press Run again in a moment, or move this role to a key with more room "
        f"(POLICY_PROVIDER / POLICY_MODEL, TEXT_MODEL_PROVIDER / TEXT_MODEL). No action was "
        f"executed. Provider said: {detail}"
    )


def _no_answer(error, seconds):
    """The error a user sees when nothing came back: what happened, and that the key is fine."""
    if isinstance(error, httpx.TimeoutException):
        detail = (
            f"no answer within {seconds:g} s (each of {ATTEMPTS} attempts). The endpoint is slow or busy —"
            " this is not a rejected key: raise JEV_HTTP_TIMEOUT to wait longer."
        )
    else:
        detail = (
            f"the connection could not be established ({ATTEMPTS} attempts). Check the network, a proxy or a VPN."
        )
    return RuntimeError(f"Model connection failed; no action executed — {detail}")


def post_json(url, key, body, headers=None):
    seconds = timeout_seconds()
    waited = 0.0
    attempt = 0
    # A rate limit gets one more try than a wire failure: waiting is the whole point of it.
    limit = max(ATTEMPTS, RATE_LIMIT_ATTEMPTS)
    while attempt < limit:
        attempt += 1
        try:
            response = CLIENT.post(url, json=body, headers=headers or {"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as error:
            # A model that is being woken up, or a blip on the wire: the same tries the
            # streaming path gives before giving up. Retrying cannot duplicate work here —
            # nothing was sent and accepted.
            if attempt < ATTEMPTS:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            raise _no_answer(error, seconds) from None
        if response.status_code in RATE_LIMIT_STATUS:
            hint = suggested_wait(response, attempt)
            pause = hint if hint is not None else 0.5 * 2 ** (attempt - 1)
            if attempt < RATE_LIMIT_ATTEMPTS:
                time.sleep(pause)
                waited += pause
                continue
            raise rate_limit_error(response, waited)
        if response.is_error:
            # Include the provider's own message so the exact cause is visible in the sidebar.
            detail = redact(" ".join(response.text.split()))[:300]
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}: {detail}; no action executed.")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError(
                "Model provider returned a non-JSON response; check that the base URL ends in /v1."
            ) from None
    raise RuntimeError("Model unavailable")


def post_stream(url, key, body, headers=None, on_delta=None):
    """POST one request and consume the SSE stream; returns (text, usage, model).

    Every text chunk is passed to on_delta as it arrives (reasoning chunks too,
    for the live "thinking" view — but only content builds the returned text).
    Raises the same RuntimeErrors as post_json so error handling stays uniform.
    Retries happen only before the first chunk, never mid-stream.
    """
    request_headers = headers or {"Authorization": f"Bearer {key}"}
    seconds = timeout_seconds()
    waited = 0.0
    attempt = 0
    limit = max(ATTEMPTS, RATE_LIMIT_ATTEMPTS)
    while attempt < limit:
        attempt += 1
        parts = []
        usage = {}
        model_id = None
        served = False
        try:
            with CLIENT.stream("POST", url, json=body, headers=request_headers) as response:
                if response.status_code in RATE_LIMIT_STATUS:
                    hint = suggested_wait(response, attempt)
                    pause = hint if hint is not None else 0.5 * 2 ** (attempt - 1)
                    if attempt < RATE_LIMIT_ATTEMPTS:
                        time.sleep(pause)
                        waited += pause
                        continue
                    raise rate_limit_error(response, waited)
                if response.is_error:
                    detail = redact(" ".join(response.read().decode("utf-8", "replace").split()))[:300]
                    raise RuntimeError(
                        f"Model provider returned HTTP {response.status_code}: {detail}; no action executed."
                    )
                for line in response.iter_lines():
                    content, usage_update, chunk_model, reasoning = _sse_chunk(line)
                    if model_id is None and chunk_model:
                        model_id = chunk_model
                    if usage_update:
                        usage.update(usage_update)
                    if reasoning and on_delta:
                        on_delta(reasoning)
                    if content:
                        served = True
                        parts.append(content)
                        if on_delta:
                            on_delta(content)
                return "".join(parts), usage, model_id
        except httpx.HTTPError as error:
            if served:
                raise RuntimeError("Model stream failed mid-response; no action executed.") from None
            if attempt < ATTEMPTS:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            raise _no_answer(error, seconds) from None
    raise RuntimeError("Model unavailable")


def _sse_chunk(line):
    """Parse one SSE line → (content, usage_update, model_id, reasoning).

    Handles the OpenAI shape (choices[].delta.content / delta.reasoning_content,
    used by any OpenAI-compatible server too) and the Anthropic event shape.
    """
    if not line or not line.startswith("data:"):
        return "", {}, None, ""
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return "", {}, None, ""
    try:
        event = json.loads(payload)
    except ValueError:
        return "", {}, None, ""
    if not isinstance(event, dict):
        return "", {}, None, ""
    model_id = event.get("model") if isinstance(event.get("model"), str) else None
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    kind = event.get("type")
    if kind == "content_block_delta":  # Anthropic
        delta = event.get("delta") or {}
        thinking = delta.get("thinking") if isinstance(delta.get("thinking"), str) else ""
        return delta.get("text") or "", {}, model_id, thinking
    if kind == "message_start":
        message = event.get("message") or {}
        return "", message.get("usage") or {}, model_id, ""
    if kind == "message_delta":
        return "", event.get("usage") or {}, model_id, ""
    choices = event.get("choices") or []
    if choices:
        delta = (choices[0] or {}).get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = ""
        if not usage:  # some servers nest usage inside the choice instead of the event
            nested = (choices[0] or {}).get("usage")
            if isinstance(nested, dict):
                usage = nested
        return delta.get("content") or "", usage, model_id, reasoning
    return "", usage, model_id, ""


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            if action.get("secret"):
                # El campo es un objetivo válido, pero su contenido nunca sale del navegador.
                element["secret"] = True
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            # The element's own label is already on its line above, so an option line that
            # repeats it as "Passengers → Adults" pays for those characters on every single
            # option of every dropdown. Only the part that identifies the option is useful.
            element["options"].append(
                {"index": target, "label": action["label"].split(" → ")[-1], "value": action["value"]}
            )
        group[target] = action
    return elements, targets, controls


def operation_catalog(targets, controls):
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    return operations


def choose(state, goal, history):
    """TypeSafe Jev when its key is present; otherwise the configured OpenAI-compatible/Anthropic provider."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return typesafe_choose(state, goal, history)
    return provider_choose(state, goal, history)


def policy_description():
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ.get("TYPESAFE_MODEL", "jev-latest")
    try:
        provider = providers.resolve("policy")
    except ValueError:
        return "no policy model configured"
    return f"{provider['name']}:{provider['model']}"


def typesafe_choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    operations = operation_catalog(targets, controls)
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
        "provider": "typesafe",
    }


POLICY_TEXT_CHARS = 2500
FIELD_TEXT_CHARS = 2000

# ── H6 · presupuesto del prompt del ejecutor ──────────────────────────────────
# Measured on a dense page (scripts/bench_prompt_size.py): 88% of the prompt was the
# element table, and one request cost up to 30.053 characters (~7.500 tokens) — three
# quarters of Groq's free minute for a single decision. The action_space itself takes
# 0.69 ms, so the prompt was the bottleneck, not the code.
PROMPT_ELEMENT_BUDGET = 120
# A native <select> is ONE element that renders one line per observed option, so the
# element count alone does not bound the prompt. Options get their own line budget, and
# a dropdown is kept whole or not at all: a select whose options are missing is one the
# model cannot choose from, which is worse than not showing it at all.
#
# 60 is the measured compromise (scripts/bench_prompt_size.py prints the whole curve) on
# a dense page holding 6 dropdowns: it keeps the 2 nearest the viewport, lands at
# 10.036 characters — under the 12.000 ceiling and a 52.7% saving — and a page with the
# two or three dropdowns a real form shows is still seen whole. Raise it to trade money
# for reach; lower it and a mission that needs a dropdown below the fold must scroll.
PROMPT_OPTION_BUDGET = 60
# How many viewport-heights away an element still counts as reachable without scrolling.
VIEWPORT_REACH = 1.5
# A select is one element carrying one line per observed option; those lines are the
# bulk of a dense table, so interactive roles are worth keeping over decorative ones.
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio", "menuitem",
    "menuitemcheckbox", "menuitemradio", "option", "switch", "tab", "searchbox", "slider",
}


def _utility(action, state, recent_nodes):
    """How likely an element is to be the next action, in points.

    Kept deliberately simple and explainable: a number that decides which elements reach
    the model has to be one a maintainer can predict, and one a test can pin.
    """
    if action.get("node") in recent_nodes:
        return 10_000  # never drop what the run is in the middle of
    rect = action.get("rect") or {}
    reach = (state.get("h") or 0) * VIEWPORT_REACH
    top = rect.get("y")
    score = 0
    if top is not None and -reach <= top <= reach:
        score += 100  # visible or one flick away
    if str(action.get("role", "")).lower() in INTERACTIVE_ROLES:
        score += 30
    if action.get("value"):
        score += 15  # a field that holds something is worth showing
    if action.get("label"):
        score += 10
    if action.get("kind") == "fill":
        score += 5
    return score


def budget_actions(actions, state, history=(), limit=PROMPT_ELEMENT_BUDGET, option_limit=PROMPT_OPTION_BUDGET):
    """Keep the elements most likely to be the next action; report the rest as omitted.

    Two budgets, because they cost different things: a plain element is one line of the
    table, while a native <select> is one element that renders a line per option. Two
    rules make the cut safe rather than merely cheap:

    * whole elements go, never part of one. Keeping a dropdown but dropping its options
      would make the right choice unrepresentable, which is worse than not showing it.
    * what the run just did is never dropped, so the loop can still recover, confirm a
      fill or commit an autocomplete suggestion after the budget has been spent.

    Everything left out stays observable through SCROLL_DOWN, SCROLL_UP and WAIT, and the
    omission is stated in the prompt rather than hidden.
    """
    recent_nodes = {h.get("node") for h in (history or ()) if h.get("node") is not None}
    # Group by node: one observed element can produce several actions (an input and its
    # combobox wrapper, or a select with all of its options).
    groups = {}
    for action in actions:
        groups.setdefault(action.get("node"), []).append(action)
    if limit is None or len(groups) <= limit:
        return actions, 0

    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -_utility(item[1][0], state, recent_nodes),
            len(item[1][0].get("label") or ""),
        ),
    )
    # Two passes, because the two kinds cost very different things. A button or a field is
    # one line; a dropdown is one line plus one per option. Filling the budget in score
    # order alone would let six dropdowns hide every button on the page, which is exactly
    # the kind of blindness that turns a cheap prompt into a failed mission.
    plain = [item for item in ranked if len(item[1]) == 1]
    selects = [item for item in ranked if len(item[1]) > 1]

    kept, omitted, option_lines, elements_kept = [], 0, 0, 0
    # Plain elements are cheap, so on a dense page they would fill every slot and leave
    # the model with no dropdown at all — which silently breaks every SELECT mission.
    # A slice of the budget is therefore kept back for the expensive groups.
    reserve = max(2, limit // 6)
    plain_ceiling = max(1, limit - reserve)
    for _node, group in plain:
        if elements_kept < plain_ceiling or not kept:
            kept.extend(group)
            elements_kept += 1
        else:
            omitted += 1
    # Two different accounts, deliberately: `elements_kept` counts ELEMENTS (a dropdown is
    # one, whatever its option count) and `option_lines` counts only what a dropdown
    # adds. Conflating them let a single 20-option select consume the whole budget.
    for _node, group in selects:
        urgent = _utility(group[0], state, recent_nodes) >= 10_000
        cost = len(group)
        if urgent:
            kept.extend(group)
            elements_kept += 1
            option_lines += cost
        elif elements_kept < limit and option_lines + cost <= (option_limit or cost):
            kept.extend(group)
            elements_kept += 1
            option_lines += cost
        else:
            omitted += 1
    if not kept:  # a budget below one element must still leave the loop something to do
        kept = ranked[0][1]
        omitted = len(groups) - 1
    # Preserve the observed order: the model reads the page top to bottom.
    order = {id(a): i for i, a in enumerate(actions)}
    kept.sort(key=lambda a: order[id(a)])
    return kept, omitted


def _policy_request(goal, state, elements, operations, history, omitted=0):
    lines = [f"GOAL: {goal}", "", f"PAGE: {state['url']} — {state['title']}"]
    if state.get("text"):
        # The indexed element table below carries the actionable detail; the free text is
        # context. Measured on the live flights mission, sending all 6000 observed
        # characters cost most of a free tier's minute per decision, so the excerpt is
        # trimmed here and the full text stays in the page state for verification.
        lines.append(f"PAGE TEXT (excerpt): {state['text'][:POLICY_TEXT_CHARS]}")
    lines.append("")
    lines.append("ELEMENTS (index · role · label · current value · operations):")
    for element in elements:
        line = f"[{element['index']}] {element['role']} · {element['label']}"
        if element.get("value"):
            line += f" · value {json.dumps(element['value'])}"
        for key in ("checked", "selected", "expanded"):
            if element.get(key) is not None:
                line += f" · {key}={json.dumps(element[key])}"
        line += f" · ops: {', '.join(element['operations'])}"
        if element.get("secret"):
            line += " · secret: fill only with a value the goal supplies; never invent one"
        lines.append(line)
        for option in element.get("options", []):
            lines.append(f"    {option['index']} {json.dumps(option['label'])} → value {json.dumps(option['value'])}")
    if omitted:
        lines.append(
            f"(+{omitted} more observed elements are not listed; scroll or WAIT to reach them)"
        )
    lines.append("")
    lines.append("AVAILABLE OPERATIONS (choose exactly one):")
    lines.extend(f"{name}: {description}" for name, description in operations.items())
    lines.append("")
    recent = [
        f"{h['step']}. {h['action']} ({h['kind']}"
        + (f", typed {json.dumps(h['text'])}" if h.get("text") else "")
        + (") — page changed" if h.get("page_changed") else ") — no observed change")
        for h in history[-10:]
    ]
    lines.append("RECENT ACTIONS:" if recent else "RECENT ACTIONS: none")
    lines.extend(recent)
    return "\n".join(lines)


def _validate_llm_choice(answer, operations, targets):
    if not isinstance(answer, dict) or answer.get("operation") not in operations:
        raise ValueError("invalid operation")
    operation = answer["operation"]
    target = None
    if operation in targets:
        target = str(answer.get("target", "")).strip()
        if target not in targets[operation]:
            raise ValueError("invalid target")
    confidence = answer.get("confidence")
    if not (type(confidence) in (int, float) and 0 <= confidence <= 1):
        confidence = 1.0
    return operation, target, float(confidence)


def provider_choose(state, goal, history):
    """One request to the configured provider returns operation and target together."""
    # H6: the element table, not the local code, was the cost. Budget it before the
    # action space is built so every index in the prompt still maps to a real element.
    actions, omitted = budget_actions(state["actions"], state, history)
    elements, targets, controls = action_space(actions)
    operations = operation_catalog(targets, controls)
    provider = providers.resolve("policy")
    user = _policy_request(goal, state, elements, operations, history, omitted)
    started = time.perf_counter()
    content, meta = None, None
    for attempt in range(2):
        message = user
        if attempt:
            message += "\n\nYour previous reply was rejected. Respond again with ONLY the JSON object."
        # Same reasoning as the text helper: a thinking model needs room for the
        # thought and the answer, and a truncated reply is an invalid choice.
        content, meta = providers.chat(provider, POLICY_SYSTEM, message, max_tokens=2048)
        try:
            answer = providers.extract_json(content)
            operation, target, confidence = _validate_llm_choice(answer, operations, targets)
            break
        except ValueError as error:
            if attempt:
                raise ValueError(f"Policy model returned an invalid choice ({error}); no action executed.") from None
    if target is not None:
        choice = targets[operation][target]["id"]
    else:
        choice = controls[operation]["id"] if operation in controls else operation
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": confidence,
        "probabilities": {choice: confidence},
        "operation_probabilities": {},
        "target_probabilities": {},
        "target_confidence": confidence if target is not None else None,
        "raw_answers": answer,
        "model": meta["model"],
        "usage": meta.get("usage", {}),
        # H6 made measurable: what the prompt cost, and what the budget left out.
        "prompt_chars": len(user),
        "prompt_elements": len(elements),
        "omitted_elements": omitted,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {
            "provider": provider["name"],
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": POLICY_SYSTEM},
                {"role": "user", "content": user},
            ],
        },
        "provider": "llm",
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        # Un campo de contraseña llega sin valor por diseño; el ayudante no debe deducir uno.
        "secret": bool(action.get("secret")),
        "page": {"title": page["title"], "text": page["text"][:FIELD_TEXT_CHARS]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    provider = providers.resolve("text")
    request = json.dumps(context)
    started = time.perf_counter()
    content, meta = None, None
    for attempt in range(2):
        message = request
        if attempt:
            message += ('\n\nYour previous reply was rejected. The goal states the value this field needs: '
                        'reply with ONLY {"text": "that value, exactly as the goal writes it"}.')
        # A reasoning model spends part of this budget thinking, and a field value is
        # short: 1024 tokens was enough for the answer and not for the thinking, which is
        # how a two-attempt retry came back empty on the live mission.
        content, meta = providers.chat(provider, TEXT_VALUE, message, max_tokens=2048)
        try:
            output = providers.extract_json(content)
            value = output["text"]
            if set(output) == {"text"} and value is None and context.get("secret"):
                # The goal does not carry this credential, the page does not supply one, and
                # the browser will not hand one over. Inventing a password is the one thing
                # an agent must never do, so the answer is "I do not have it", not a guess.
                return None, {
                    "model": provider["model"],
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "usage": meta.get("usage", {}),
                    "unavailable": "the goal supplies no value for this field",
                }
            if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as rejected:
            # Say what the model actually answered: "nothing typed" alone sent a maintainer
            # hunting through logs for a field this layer owns.
            sample = redact(" ".join((content or "").split()))[:160]
            why = f"{type(rejected).__name__}: {rejected}" if str(rejected) else "no usable text"
            last = f"last reply: {sample!r}" if sample else "last reply: empty"
            continue  # a small model answers {"text": null} now and then; ask once more
        return value, {
            "model": provider["model"],
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": meta.get("usage", {}),
        }
    raise ValueError(f"Text helper returned no valid field value ({why}; {last}); nothing typed.") from None


def planning_config(goal=None):
    """The planner provider config, or None to keep the original single-goal loop.

    Enabled by configuration or derived from a free key (one key is enough to
    run the whole agent), so a fresh install plans its missions out of the box.

    H12: a planner call is a full reasoning call — 21 s measured on a real install, against
    402 ms for the executor — and in the 11-mission battery it *hurt*: it invented domains
    the user never wrote and the executor followed them (H9). So with a goal in hand the
    mission is routed first, and a mission that is one gesture pays no planner call.
    Without a goal the old behaviour stands: any caller that has not opted into routing
    keeps exactly what it had.
    """
    if not providers.planner_enabled():
        return None
    if goal is not None:
        from . import routing

        planned, _decision = routing.needs_a_plan(goal)
        if not planned:
            return None
    return providers.resolve("planner")


def _validate_steps(answer):
    steps = answer.get("steps") if isinstance(answer, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > MAX_PLAN_STEPS:
        raise ValueError("invalid steps")
    cleaned = []
    for step in steps:
        if not isinstance(step, str) or not step.strip() or len(step) > 500:
            raise ValueError("invalid step")
        cleaned.append(step.strip())
    return cleaned


# ── H9 · un plan no puede nombrar un dominio que nadie ha visto todavía ─────────

# El fallo medido: en la batería de once misiones el planificador inventó dominios que el
# usuario nunca escribió — M1 derivó a AFRINIC — y el ejecutor los siguió. Un plan es una
# instrucción, y una instrucción que lleva a un sitio que nadie ha visto convierte al
# ejecutor en un cursor obediente de una alucinación.
#
# El ancla no es el objetivo, porque el objetivo no basta: «buscar NVIDIA NIM» no menciona
# nvidia.com y sin embargo llegar ahí ES la misión. El ancla es todo lo que el agente ya
# vio: lo que escribió el objetivo, la URL de la página y el texto que hay en pantalla. Un
# dominio que aparece en la página es descubrible, así que nombrarlo en el plan no es
# inventarlo. Uno que no aparece en ninguno de los tres sí lo es.

# Solo se reconoce como dominio lo que trae esquema, lo que empieza por www., o lo que
# termina en un TLD de esta lista. El reconocimiento conservador es lo importante: lo que
# no se reconoce como dominio no se restringe, así que un falso positivo no rompe ninguna
# misión legítima. Sin lista, «configurar el archivo config.js» sería un dominio, y el paso
# legítimo que lo menciona se eliminaría.
KNOWN_TLDS = frozenset("""
    com net org edu gov mil int info biz name pro coop museum aero jobs mobi travel
    io ai app dev co me tv cc xyz online site tech cloud store shop blog wiki live life
    world today news media network systems solutions agency digital design studio group
    ac ad ae ar at au be bg br by ca ch cl cn cz de dk eg es eu fi fr gr hk hr hu id ie
    il in iq ir it jp kr lt lu lv ma mx my nl no nz pl pt qa ro rs ru se sg sk th tr tw
    ua uk us vn za
""".split())

DOMAIN = re.compile(r"\b(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24})\b", re.I)


def mentioned_domains(*texts):
    """Los dominios que un texto nombra, en minúsculas y sin esquema ni ruta."""
    found = set()
    for text in texts:
        if not text:
            continue
        for match in DOMAIN.finditer(str(text)):
            host = match.group(1).lower().strip(".")
            if host.rsplit(".", 1)[-1] in KNOWN_TLDS:
                found.add(host)
    return found


def _same_site(one, other):
    """Se comparan por etiquetas, para que www.ejemplo.com y ejemplo.com sean el mismo sitio."""
    return one == other or one.endswith("." + other) or other.endswith("." + one)


def plan_domains(mission, page):
    """Los dominios que la misión y la página actual ya han puesto a la vista."""
    page = page or {}
    return mentioned_domains(mission, page.get("url"), page.get("title"), page.get("text"))


def plan_domains_enabled():
    """Válvula de seguridad: si el filtro estorba a una misión legítima, se apaga con off.

    Por defecto no hay nada que configurar, que es como se configura el resto del agente.
    """
    return (os.environ.get("JEV_PLAN_DOMAINS") or "").strip().lower() not in {"off", "0", "false", "no"}


def anchored_steps(steps, allowed):
    """Quita del plan los pasos que llevan a un dominio que nadie ha visto.

    Se quita el paso, no el plan entero: un plan de cuatro pasos con uno alucinado sigue
    siendo un plan útil, y el presupuesto de pasos no conviene gastarlo en volver a
    preguntar. Si no queda ninguno se devuelve la lista vacía, y quien llama cae al bucle
    de objetivo único, que es el comportamiento seguro y ya existía.
    """
    if not allowed or not plan_domains_enabled():
        return list(steps)
    kept = []
    for step in steps:
        invented = [
            host for host in mentioned_domains(step)
            if not any(_same_site(host, site) for site in allowed)
        ]
        if not invented:
            kept.append(step)
    return kept


def _planner_request(mission, page, *, reason=None, plan=None, plan_index=0, history=None, allowed=None):
    lines = ["MISSION: " + mission, "", f"PAGE: {page.get('url', '')} — {page.get('title', '')}"]
    if allowed and plan_domains_enabled():
        shown = ", ".join(sorted(allowed)[:12])
        lines += [
            "",
            f"DOMAINS ALREADY IN VIEW: {shown}",
            "Do not name or navigate to any other domain. If the mission needs a site that is not on",
            "that list, write the step as what to do there ('open the first result') instead of naming",
            "a domain: the executor can only reach what is on the page.",
        ]
    if reason:
        done = ["  ✓ " + step for step in (plan or [])[:plan_index]]
        remaining = ["  ✗ " + step for step in (plan or [])[plan_index:]]
        lines += ["", "PREVIOUS PLAN (✓ completed, ✗ not completed):", *(done or ["  none"]), *remaining]
        lines += ["", "PROBLEM: " + reason]
        if history:
            lines += ["", "RECENT ACTIONS:"]
            lines += [f"{h.get('step', '')}. {h.get('action', '')} ({h.get('kind', '')})" for h in history[-6:]]
        lines += ["", "Produce a corrected plan for the REMAINING work only. Reply with the JSON steps list."]
    else:
        lines += ["", "Produce the ordered checklist of browser steps for the mission. Reply with the JSON steps list."]
    return "\n".join(lines)


PLANNER_ATTEMPTS = 2


def _ask_for_plan(provider, user):
    """One planner request, parsed and validated."""
    content, _meta = providers.chat(provider, PLANNER_SYSTEM, user, max_tokens=1024)
    return _validate_steps(providers.extract_json(content))


def _plan_with_one_retry(provider, user):
    """Ask for the plan, and ask once more when the reply came back unusable.

    Measured on 2026-09-25 (scripts/bench_profiles.py, 10 missions x 2 token budgets): one planner
    call in ten came back **empty** from NVIDIA's GLM endpoint - and at exactly the same rate with
    1024 and with 2048 tokens, so it is not a truncated answer and a bigger budget does not fix it.
    The planner runs once per mission, so a second try is cheap; losing the plan is not fatal (the
    agent falls back to the single-goal loop) but the checklist is worth one more question.

    Only an unusable *reply* is retried. A connection failure is already retried three times inside
    model.post_json and is raised here as it is.
    """
    last = None
    for _attempt in range(PLANNER_ATTEMPTS):
        try:
            return _ask_for_plan(provider, user)
        except ValueError as error:  # empty reply, unparseable JSON, or a rejected step list
            last = error
    raise last


def plan_steps(mission, page):
    """The planner decomposes the mission into a short ordered checklist of browser steps."""
    provider = providers.resolve("planner")
    allowed = plan_domains(mission, page)
    steps = _plan_with_one_retry(provider, _planner_request(mission, page, allowed=allowed))
    return anchored_steps(steps, allowed)


def replan_steps(mission, plan, plan_index, reason, page, history):
    """Replacement steps for the remaining plan after a blocked or stalled step."""
    provider = providers.resolve("planner")
    allowed = plan_domains(mission, page)
    user = _planner_request(
        mission, page, reason=reason, plan=plan, plan_index=plan_index, history=history, allowed=allowed
    )
    return anchored_steps(_plan_with_one_retry(provider, user), allowed)
