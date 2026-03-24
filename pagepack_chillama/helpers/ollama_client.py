"""
ollama_client.py
────────────────
Thin wrapper around the Ollama local REST API.
Handles connection testing, model listing, and chat completion.
All methods return structured dicts — never raise into the caller.

Default base URL: http://localhost:11434
"""

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional

DEFAULT_BASE_URL = "http://localhost:11434"

# Timeout (seconds) for quick probes vs generation
_PROBE_TIMEOUT = 5
_GENERATE_TIMEOUT = 300  # 5 min ceiling for long generations


class OllamaClient:
    """Synchronous Ollama REST client — stdlib only, no dependencies."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    # ── helpers ──────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _read_response(resp) -> bytes:
        """Read full response body, handling chunked/gzip transparently."""
        return resp.read()

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 timeout: int = _PROBE_TIMEOUT) -> dict:
        """
        Make an HTTP request. Returns:
            {"ok": True, "status": int, "data": <parsed JSON or raw text>}
        or  {"ok": False, "error": str}
        """
        url = self._url(path)
        headers = {"Content-Type": "application/json"} if body else {}
        data_bytes = json.dumps(body).encode("utf-8") if body else None

        req = urllib.request.Request(url, data=data_bytes, headers=headers,
                                     method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = self._read_response(resp)
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                parsed = raw.decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "data": parsed}
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return {"ok": False, "error": f"HTTP {exc.code}: {body_text[:500]}"}
        except urllib.error.URLError as exc:
            return {"ok": False, "error": f"Connection failed: {exc.reason}"}
        except OSError as exc:
            return {"ok": False, "error": f"OS error: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── public API ──────────────────────────────────────────────────────

    def ping(self) -> dict:
        """
        Quick connection check (GET /).
        Returns {"ok": True/False, "version": str_or_None, "error": str_or_None}
        """
        result = self._request("GET", "/")
        if result["ok"]:
            version = None
            if isinstance(result["data"], dict):
                version = result["data"].get("version")
            return {"ok": True, "version": version, "error": None}
        return {"ok": False, "version": None, "error": result["error"]}

    def list_models(self) -> dict:
        """
        GET /api/tags — list locally available models.
        Returns {"ok": True, "models": [{"name":..., "size":..., ...}, ...]}
        or      {"ok": False, "models": [], "error": str}
        """
        result = self._request("GET", "/api/tags")
        if not result["ok"]:
            return {"ok": False, "models": [], "error": result["error"]}

        data = result["data"]
        if not isinstance(data, dict):
            return {"ok": False, "models": [],
                    "error": "unexpected response format"}

        raw_models = data.get("models", [])
        models = []
        for m in raw_models:
            if isinstance(m, dict):
                models.append({
                    "name": m.get("name", "unknown"),
                    "size": m.get("size", 0),
                    "modified_at": m.get("modified_at", ""),
                    "digest": m.get("digest", ""),
                    "parameter_size": m.get("details", {}).get("parameter_size", ""),
                    "family": m.get("details", {}).get("family", ""),
                })
        return {"ok": True, "models": models, "error": None}

    def chat(self, model: str, messages: list,
             temperature: float = 0.7,
             stream: bool = False) -> dict:
        """
        POST /api/chat — synchronous (non-streaming) chat completion.

        messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]

        Returns:
            {"ok": True, "content": str, "model": str,
             "total_duration": int, "eval_count": int, "error": None}
        or  {"ok": False, "content": "", "error": str}
        """
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        result = self._request("POST", "/api/chat", body=body,
                               timeout=_GENERATE_TIMEOUT)
        if not result["ok"]:
            return {"ok": False, "content": "", "error": result["error"]}

        data = result["data"]
        if not isinstance(data, dict):
            return {"ok": False, "content": "",
                    "error": "unexpected response format"}

        msg = data.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        return {
            "ok": True,
            "content": content,
            "model": data.get("model", model),
            "total_duration": data.get("total_duration", 0),
            "eval_count": data.get("eval_count", 0),
            "error": None,
        }

    def generate(self, model: str, prompt: str,
                 system: str = "",
                 temperature: float = 0.7) -> dict:
        """
        POST /api/generate — single-shot generate (non-streaming).
        Fallback path if /api/chat is unavailable on older Ollama.

        Returns same shape as chat().
        """
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if system:
            body["system"] = system

        result = self._request("POST", "/api/generate", body=body,
                               timeout=_GENERATE_TIMEOUT)
        if not result["ok"]:
            return {"ok": False, "content": "", "error": result["error"]}

        data = result["data"]
        if not isinstance(data, dict):
            return {"ok": False, "content": "",
                    "error": "unexpected response format"}

        return {
            "ok": True,
            "content": data.get("response", ""),
            "model": data.get("model", model),
            "total_duration": data.get("total_duration", 0),
            "eval_count": data.get("eval_count", 0),
            "error": None,
        }
