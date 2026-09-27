import json
import os
import sys
import time
import urllib.request

KEY = os.environ.get("NVIDIA_API_KEY", "")
if not KEY:
    print("NVIDIA_API_KEY missing", file=sys.stderr)
    sys.exit(1)

CANDIDATES = [
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
    ("deepseek-ai/deepseek-v4.1-flash", {}),
    ("mistralai/mistral-large-2-instruct", {}),
    ("nv-mistralai/mistral-nemo-12b-instruct", {}),
    ("google/gemma-3-12b-it", {}),
    ("nvidia/llama-3.1-nemotron-51b-instruct", {}),
    ("moonshotai/kimi-k2.6", {}),
    ("z-ai/glm-5.3-flash", {}),
]

PROMPTS = [
    (
        "PLANNER",
        "You are the planning layer of a browser automation agent.",
        "MISSION: Book the cheapest direct flight from Barcelona to Rome next Friday, one adult",
        1024,
    ),
    (
        "ROUTING 1",
        (
            "Respond ONLY with a JSON object {\"choice\": \"browser\"|\"orchestrated\"}. "
            "Use \"browser\" for missions finished on the current tab, "
            "and \"orchestrated\" for missions needing search, files, code or multi-site workflows."
        ),
        "MISSION: Search the web for hotel prices in Rome",
        2048,
    ),
    (
        "ROUTING 2",
        (
            "Respond ONLY with a JSON object {\"choice\": \"browser\"|\"orchestrated\"}. "
            "Use \"browser\" for missions finished on the current tab, "
            "and \"orchestrated\" for missions needing search, files, code or multi-site workflows."
        ),
        "MISSION: Click on the first link on this page",
        2048,
    ),
    (
        "TEXT",
        "Return a JSON object with exactly one key, text: the exact string to enter in the selected field.",
        "GOAL: Fly from Barcelona to Rome\nFIELD: Departure city",
        2048,
    ),
]


def test_model(model_id, extra_params):
    print(f"=== Testing {model_id} (params={extra_params}) ===")
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    results = []

    for name, sys_msg, user_msg, max_tok in PROMPTS:
        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": max_tok,
            "temperature": 0.2,
            **extra_params,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
            },
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as ans:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                resp = json.loads(ans.read().decode("utf-8"))
                content = resp["choices"][0]["message"]["content"]
                print(
                    f"  [{name:10}] {elapsed_ms:5} ms | response: {content[:80].strip()!r}"
                )
                results.append((name, elapsed_ms, True, content))
        except Exception as err:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            print(f"  [{name:10}] {elapsed_ms:5} ms | ERROR: {err}")
            results.append((name, elapsed_ms, False, str(err)))

    print()
    return results


for model_id, params in CANDIDATES:
    test_model(model_id, params)
