"""Lists the Gemini models this GEMINI_API_KEY can call with generateContent (Flash ones first).
    python scripts/list_gemini_models.py"""
import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()
req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                             headers={"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")})
models = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("models", [])
names = [m["name"].removeprefix("models/") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
for n in sorted(names, key=lambda n: ("flash" not in n, n)):
    print(n)
