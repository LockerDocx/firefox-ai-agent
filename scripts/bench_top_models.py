import json
import os
import sys
import time
import urllib.request

KEY = os.environ.get("NVIDIA_API_KEY", "")
if not KEY:
    print("NVIDIA_API_KEY missing", file=sys.stderr)
    sys.exit(1)

# Add repo to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jev_ultrafast import providers
from jev_ultrafast.questions import PLANNER_SYSTEM, TEXT_VALUE
from scripts.bench_routing import ROUTING_SYSTEM
from scripts.routing_cases import ROUTING_CASES

CANDIDATE_MODELS = [
    ("google/gemma-4-31b-it", "Google Gemma 4 31B IT"),
    ("meta/llama-3.2-11b-vision-instruct", "Meta Llama 3.2 11B Vision"),
    ("openai/gpt-oss-20b", "OpenAI GPT-OSS 20B"),
]

def benchmark_model(model_id, display_name):
    print(f"==================================================")
    print(f" Benchmarking {display_name} ({model_id})")
    print(f"==================================================")
    
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    
    # 1. Test Text Writer
    print("--- 1. Text Writer (4 cases) ---")
    text_cases = [
        ("Reserve a table for 4 people", "Number of people", "4"),
        ("Send invoice to maria@example.com", "Email address", "maria@example.com"),
        ("Fly from Barcelona to Rome", "Departure city", "Barcelona"),
        ("Book room for 2026-10-12", "Check-in date", "2026-10-12"),
    ]
    text_latencies = []
    text_exact = 0
    for goal, field, expected in text_cases:
        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {"role": "user", "content": f"GOAL: {goal}\nFIELD: {field}"},
            ],
            "max_tokens": 512,
            "temperature": 0.1,
        }
        if "gpt-oss" in model_id:
            body["reasoning_effort"] = "low"
            
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as res:
                ms = round((time.perf_counter() - t0) * 1000)
                text_latencies.append(ms)
                raw = json.loads(res.read().decode())["choices"][0]["message"]["content"]
                val = providers.extract_json(raw).get("text", "")
                is_exact = (str(val).strip() == expected)
                if is_exact: text_exact += 1
                print(f"  [{ms:4} ms] {field:20} -> got {val!r} (expected {expected!r}) {'✓' if is_exact else '✗'}")
        except Exception as e:
            ms = round((time.perf_counter() - t0) * 1000)
            print(f"  [{ms:4} ms] {field:20} -> ERROR: {e}")
            
    # 2. Test Routing Battery (12 stratified cases)
    print("\n--- 2. Routing Decision (12 cases) ---")
    stride = max(1, len(ROUTING_CASES) // 12)
    sample_cases = ROUTING_CASES[::stride][:12]
    routing_latencies = []
    routing_hits = 0
    for case in sample_cases:
        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": ROUTING_SYSTEM},
                {"role": "user", "content": f"MISSION: {case['mission']}"},
            ],
            "max_tokens": 512,
            "temperature": 0.1,
        }
        if "gpt-oss" in model_id:
            body["reasoning_effort"] = "low"
            
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as res:
                ms = round((time.perf_counter() - t0) * 1000)
                routing_latencies.append(ms)
                raw = json.loads(res.read().decode())["choices"][0]["message"]["content"]
                ans = providers.extract_json(raw).get("choice")
                is_hit = (ans == case["expected"])
                if is_hit: routing_hits += 1
                print(f"  [{ms:4} ms] {case['mission'][:40]:42} -> got {ans!r} (expected {case['expected']!r}) {'✓' if is_hit else '✗'}")
        except Exception as e:
            ms = round((time.perf_counter() - t0) * 1000)
            print(f"  [{ms:4} ms] {case['mission'][:40]:42} -> ERROR: {e}")

    # Summary
    avg_text = round(sum(text_latencies) / len(text_latencies)) if text_latencies else 0
    avg_rout = round(sum(routing_latencies) / len(routing_latencies)) if routing_latencies else 0
    print(f"\nRESULTS FOR {display_name}:")
    print(f"  Text Latency:    {avg_text} ms avg | Exact: {text_exact}/{len(text_cases)}")
    print(f"  Routing Latency: {avg_rout} ms avg | Accuracy: {routing_hits}/{len(sample_cases)}")
    print("\n")

for mid, name in CANDIDATE_MODELS:
    benchmark_model(mid, name)
