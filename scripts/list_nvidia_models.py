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

try:
    with urllib.request.urlopen(req, timeout=15) as ans:
        data = json.loads(ans.read().decode())
        ids = [m["id"] for m in data.get("data", [])]
        print(f"Total models in NVIDIA catalogue: {len(ids)}\n")

        chat_models = []
        for model_id in ids:
            low = model_id.lower()
            if any(
                bad in low
                for bad in (
                    "embed",
                    "rerank",
                    "guard",
                    "clip",
                    "sdxl",
                    "flux",
                    "audio",
                    "whisper",
                    "tts",
                    "stt",
                    "canary",
                    "parakeet",
                    "retriever",
                    "vista",
                    "esm",
                    "evo",
                    "cad",
                    "omni",
                )
            ):
                continue
            chat_models.append(model_id)

        print(f"Chat models ({len(chat_models)}):")
        for cm in chat_models:
            print(" ", cm)
except Exception as err:
    print("Error fetching models:", err, file=sys.stderr)
    sys.exit(1)
