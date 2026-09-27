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

from jev_ultrafast import providers  # noqa: E402
from jev_ultrafast.questions import PLANNER_SYSTEM  # noqa: E402

missions = [
    "Book the cheapest direct flight from Barcelona to Rome next Friday, one adult",
    "Find the schedule of the Sagrada Familia and tell me if it opens on Sunday morning",
    "On the page I am looking at, order the results by price from low to high",
    "Reserve a table for four people on Friday at 21:00 in a restaurant near the Gothic Quarter",
    "Find a hotel in Lisbon for two nights from 12 October, with breakfast, and open the cheapest",
]

url = "https://integrate.api.nvidia.com/v1/chat/completions"

print("==================================================")
print(" Testing Planner on Meta Llama 3.2 11B Vision")
print("==================================================")

valid_plans = 0
latencies = []

for mission in missions:
    body = {
        "model": "meta/llama-3.2-11b-vision-instruct",
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": f"MISSION: {mission}"},
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
    }
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            ms = round((time.perf_counter() - t0) * 1000)
            latencies.append(ms)
            raw = json.loads(res.read().decode())["choices"][0]["message"]["content"]
            parsed = providers.extract_json(raw)
            steps = parsed.get("steps")
            is_valid = (
                isinstance(steps, list)
                and 1 <= len(steps) <= 12
                and all(isinstance(s, str) and s.strip() for s in steps)
            )
            if is_valid:
                valid_plans += 1
            n = len(steps) if isinstance(steps, list) else 0
            print(f"[{ms:4} ms] {mission[:40]:42} -> valid: {is_valid} | steps: {n}")
            print(f"       raw: {raw[:100]!r}\n")
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        print(f"[{ms:4} ms] {mission[:45]:47} -> ERROR: {e}\n")

avg_ms = round(sum(latencies) / len(latencies)) if latencies else 0
print(f"SUMMARY: Average Planner Latency = {avg_ms} ms | Valid Plans = {valid_plans}/{len(missions)}")
