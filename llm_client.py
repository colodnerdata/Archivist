import sys
import json

import requests

_TIMEOUT = 240


def check_ollama(base_url: str, models_required: list[str]) -> None:
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=10)
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to Ollama at {base_url}")
        print("Start Ollama with: ollama serve")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"ERROR: Ollama request failed: {e}")
        sys.exit(1)

    available = {m["name"] for m in resp.json().get("models", [])}
    missing = [m for m in models_required if m not in available]
    if missing:
        for m in missing:
            print(f"ERROR: Model '{m}' not found in Ollama.")
            print(f"Pull it with: ollama pull {m}")
        sys.exit(1)


def generate(
    base_url: str,
    model: str,
    prompt: str,
    temperature: float = 0.1,
    stream: bool = False,
    stream_to_stdout: bool = False,
) -> str:
    resp = requests.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": stream, "options": {"temperature": temperature}},
        timeout=_TIMEOUT,
        stream=stream,
    )
    resp.raise_for_status()

    if not stream:
        return resp.json()["response"]

    chunks: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue

        raw = line.strip()
        if raw.startswith("data:"):
            raw = raw[5:].strip()

        payload = json.loads(raw)
        piece = payload.get("response", "")
        if piece:
            chunks.append(piece)
            if stream_to_stdout:
                print(piece, end="", flush=True)

        if payload.get("done"):
            break

    if stream_to_stdout:
        print()

    return "".join(chunks)


def chat_with_image(base_url: str, model: str, prompt: str, image_b64: str, temperature: float = 0.3) -> str:
    resp = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]
