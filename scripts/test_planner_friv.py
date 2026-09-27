import json
import os
import sys
import time
import urllib.request

KEY = os.environ.get("NVIDIA_API_KEY", "")
if not KEY:
    print("NVIDIA_API_KEY missing", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jev_ultrafast.questions import PLANNER_SYSTEM

url = "https://integrate.api.nvidia.com/v1/chat/completions"

models = [
    ("meta/llama-3.2-11b-vision-instruct", {}),
    ("google/gemma-4-31b-it", {}),
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
]

mission = "go, and open any game of friv.com"

print(f"Mission: {mission!r}\n")

for model_id, params in models:
    print(f"=== Testing {model_id} ===")
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": f"MISSION: {mission}"},
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        **params,
    }
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            ms = round((time.perf_counter() - t0) * 1000)
            raw = json.loads(res.read().decode())["choices"][0]["message"]["content"]
            print(f"[{ms} ms]\n{raw}\n")
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        print(f"[{ms} ms] ERROR: {e}\n")
