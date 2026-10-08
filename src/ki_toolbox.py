"""Client for TU Braunschweig's KI-Toolbox chat API (custom, not OpenAI-compatible).

Adapted from RAG-implementation/src/llm_client.py, which found the API's quirks:
- POST to the full URL (.../api/v1/chat/send) with a Bearer token; the system prompt
  goes in `customInstructions`. There is no temperature and no structured output.
- The reply streams as JSON lines: "chunk" events add text, and a "done" event carries
  the full text and the token counts.
- HTTP 429 ("rateLimit.throttled") and dropped connections mean "come back later", so
  both are retried with exponential backoff.
- Every call creates a chat in the token owner's web history (deleted after 14 days).
"""

import json
import random
import time

import requests

TIMEOUT_S = 180
MAX_ATTEMPTS = 6
BASE_BACKOFF_S = 4.0
RETRY_STATUS = {429, 500, 502, 503, 504}
NETWORK_ATTEMPTS = 5
NETWORK_BACKOFF_S = 2.0
NETWORK_ERRORS = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


def call_ki_toolbox(prompt, system_prompt, model, token, url):
    """Send one prompt. Returns (reply text, the done event: token counts and thread id)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Content-Type": "application/json"}
    payload = {"thread": None, "prompt": prompt, "model": model,
               "customInstructions": system_prompt, "hideCustomInstructions": True}
    for attempt in range(NETWORK_ATTEMPTS):
        try:
            return _read_stream(_post_with_retry(url, headers, payload))
        except NETWORK_ERRORS:
            if attempt == NETWORK_ATTEMPTS - 1:
                raise
            time.sleep(NETWORK_BACKOFF_S * 2**attempt)


def _read_stream(response):
    text, done = "", {}
    for line in response.iter_lines():
        if not line:
            continue
        event = json.loads(line.decode("utf-8"))  # decoded explicitly: replies contain umlauts
        if event.get("type") == "chunk":
            text += event.get("content", "")
        elif event.get("type") == "done":
            done = {k: v for k, v in event.items() if k not in ("response", "customInstructions")}
            text = event.get("response") or text
            break
    return text, done


def _post_with_retry(url, headers, payload):
    """POST, retrying on 429/5xx with exponential backoff and jitter."""
    for attempt in range(MAX_ATTEMPTS):
        response = requests.post(url, headers=headers, json=payload, stream=True, timeout=TIMEOUT_S)
        if response.status_code not in RETRY_STATUS or attempt == MAX_ATTEMPTS - 1:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After", "")
        delay = float(retry_after) if retry_after.strip().isdigit() else BASE_BACKOFF_S * 2**attempt
        response.close()
        time.sleep(delay + random.uniform(0, delay * 0.25))
