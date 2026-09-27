import json
import os
import sys
import time
import urllib.request

KEY = os.environ.get("NVIDIA_API_KEY", "")
if not KEY:
    print("NVIDIA_API_KEY missing", file=sys.stderr)
    sys.exit(1)

req = urllib.request.Request(
    "https://integrate.api.nvidia.com/v1/models",
    headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"},
)

with urllib.request.urlopen(req, timeout=15) as ans:
    data = json.loads(ans.read().decode())
    all_ids = [m["id"] for m in data.get("data", [])]

print(f"Testing {len(all_ids)} models with a 5-token ping...")

working = []
url = "https://integrate.api.nvidia.com/v1/chat/completions"

for model_id in all_ids:
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
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
        with urllib.request.urlopen(req, timeout=10) as ans:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            resp = json.loads(ans.read().decode("utf-8"))
            content = resp["choices"][0]["message"]["content"]
            print(f"✓ {model_id:50} | {elapsed_ms:5} ms | {content!r}")
            working.append((model_id, elapsed_ms, content))
    except Exception as err:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        err_str = str(err)
        if "404" in err_str:
            status = "404 Not Found"
        elif "500" in err_str:
            status = "500 Server Error"
        elif "timed out" in err_str or "Timeout" in err_str:
            status = "Timeout (>10s)"
        else:
            status = err_str[:40]
        print(f"✗ {model_id:50} | {elapsed_ms:5} ms | {status}")

print(f"\nSummary: {len(working)} working models out of {len(all_ids)}:")
for m, ms, c in sorted(working, key=lambda x: x[1]):
    print(f"  {m:50} -> {ms} ms")
