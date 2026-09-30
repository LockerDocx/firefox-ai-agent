"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from . import routing
from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text, plan_steps, planning_config, replan_steps
from .questions import MAX_REPLANS, MAX_STEPS

# A real navigation takes seconds to settle. One immediate re-observation used to abandon the
# mission in transit, so the loop waits this long for the page to stop changing (H10).
OBSERVE_RETRY_SECONDS = 10.0
OBSERVE_RETRY_INTERVAL = 0.2


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, browser=None):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        # H12: the route is decided before the planner is paid for, and kept in the state
        # so the sidebar can say which route the mission took and why.
        self.route, self.route_decision = routing.needs_a_plan(task)
        self.planner = planning_config(task) if self.route else None
        self.pending_text = None
        self.browser = browser if browser is not None else Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
            if self.planner:
                try:
                    steps = plan_steps(task, page)
                    if steps:
                        plan = steps
                except (RuntimeError, ValueError):
                    pass  # a failing planner falls back to the original single-goal loop
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal=task,
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            replans=0,
            planner=self.planner["model"] if self.planner else None,
            route=self.route,
            route_reason=self.route_decision["reason"] if self.route_decision else None,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def directive(self):
        """The full mission for single-goal runs; mission plus current-step focus for planned runs."""
        state = self.state
        plan = state.get("plan") or []
        if len(plan) <= 1:
            return state["goal"]
        index = min(state.get("plan_index", 0), len(plan) - 1)
        lines = ["{} {}. {}".format("✓" if k < index else "·", k + 1, step) for k, step in enumerate(plan)]
        return (
            f"{state['goal']}\n\n"
            "EXECUTION PLAN (✓ steps are already complete; do not repeat them):\n"
            + "\n".join(lines)
            + f"\n\nCURRENT STEP {index + 1}/{len(plan)}: {plan[index]}\n"
            "Advance the current step only."
        )

    def _replan(self, reason):
        """Replace the remaining steps via the planner; bounded so a broken planner cannot loop."""
        state = self.state
        if not getattr(self, "planner", None) or state.get("replans", 0) >= MAX_REPLANS:
            return False
        try:
            steps = replan_steps(
                state["goal"],
                state.get("plan") or [],
                state.get("plan_index", 0),
                reason,
                state["page"],
                state["history"],
            )
        except (RuntimeError, ValueError):
            return False
        if not steps:
            return False
        state["plan"] = (state.get("plan") or [])[: state.get("plan_index", 0)] + steps
        state["replans"] = state.get("replans", 0) + 1
        return True

    def _suggestion_for_repeated_fill(self, page, action, selected, text):
        """The offered suggestion that commits a fill the model is repeating.

        Measured on the live flights mission: the destination field already held
        "London", the suggestion "London, United Kingdom" was on screen, and the model
        typed the same value into the same field twice more, and again, until the run's
        budget was gone. A repeated fill whose value the field already holds is not an
        instruction to type again: the goal needs the suggestion clicked.
        """
        previous = self.state["history"][-1] if self.state["history"] else None
        if not previous or previous.get("kind") != "fill":
            return None
        # The same field is observed twice — the input and its combobox wrapper are separate
        # elements — so the last action, not the element id, is what says "we just typed this".
        if (previous.get("text") or "").strip().lower() != text.strip().lower():
            return None
        label = (action.get("label") or "").strip().lower()
        previous_label = (previous.get("action") or "").strip().lower()
        if previous_label != label:
            return None
        # The field is observed more than once (input and combobox wrapper) and only one of
        # those elements carries the text, so "already filled" is asked of the whole field.
        wanted = text.strip().lower()
        holds = any((candidate.get("label") or "").strip().lower() == label
                    and str(candidate.get("value") or "").strip().lower().startswith(wanted)
                    for candidate in page.get("actions") or [])
        if not holds:
            return None
        for candidate in page.get("actions") or []:
            if candidate.get("id") == action.get("id") or candidate.get("kind") != "click":
                continue
            if wanted and wanted in candidate.get("label", "").strip().lower():
                return candidate
        return None

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                # A real navigation takes seconds. Re-observing once, immediately, gave up
                # mid-flight and killed the mission in transit (H10, measured on the live
                # Wikipedia mission). Wait out the navigation before giving the decision back.
                state["decision"] = None
                state["status"] = "ready"
                deadline = time.monotonic() + OBSERVE_RETRY_SECONDS
                while True:
                    try:
                        state["page"] = state["browser"].observe(screenshot=self.screenshots)
                        break
                    except StalePage:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(OBSERVE_RETRY_INTERVAL)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(state["page"], self.directive(), state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                plan = state.get("plan") or [state["goal"]]
                if selected == "DONE" and len(plan) > 1 and state["plan_index"] < len(plan) - 1:
                    # A finished step continues to the next one; only the last DONE ends the run.
                    state["plan_index"] += 1
                    state["status"] = "ready"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                blocked_reason = "The executor reported that no supported operation can progress."
                if selected == "BLOCKED" and self._replan(blocked_reason):
                    state["status"] = "ready"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = len(plan) if selected == "DONE" else state.get("plan_index", 0)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper, suggestion = None, None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(self.directive(), action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    if text is None:
                        # A secret field with no value to put in it. Typing anything would
                        # mean inventing a credential, so the run stops here and says why:
                        # clicking Submit on an empty password field is what produced the
                        # 10-step login loop measured on saucedemo (H11).
                        state["status"] = "blocked"
                        state["blocked_reason"] = (
                            f"'{action['label']}' needs a value the goal does not supply. "
                            "Add it to the goal and run again; the agent will not invent it."
                        )
                        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                        return self.snapshot()
                    self.pending_text = (context, text, helper)
                    # A value typed into a password field never reaches the run history,
                    # the sidebar or runs.jsonl: the agent can use it, nobody can read it.
                    shown = "•••" if action.get("secret") else text
                    state["text_calls"].append({**helper, "field": action["label"], "value": shown})
                suggestion = self._suggestion_for_repeated_fill(page, action, selected, text)
                if suggestion is not None:
                    # The typing is already done; committing it is the step that remains.
                    action, text = suggestion, None
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": "•••" if action.get("secret") else text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "recovered": suggestion["label"] if suggestion is not None else None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            stalled = len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
            if stalled and self._replan("Three consecutive actions changed nothing on the page."):
                state["status"] = "ready"
            else:
                state["status"] = "blocked" if stalled else "ready"
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
