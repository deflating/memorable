"""Inference client for Memorable daemon.

Calls an OpenAI-compatible API (MLX server on Mac Mini, or DeepSeek fallback).
Handles primary/fallback switching transparently.
"""

import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

DEFAULT_PRIMARY = "http://192.168.68.58:8400/v1/chat/completions"
DEFAULT_FALLBACK = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_TIMEOUT = 15  # seconds for primary (local network)
FALLBACK_TIMEOUT = 60  # seconds for DeepSeek


class InferenceClient:
    """Calls a local or remote OpenAI-compatible chat API."""

    def __init__(
        self,
        primary_url: str = DEFAULT_PRIMARY,
        fallback_url: str = DEFAULT_FALLBACK,
        fallback_key: str = "",
        primary_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
        fallback_model: str = "deepseek-chat",
    ):
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.fallback_key = fallback_key
        self.primary_model = primary_model
        self.fallback_model = fallback_model

        self._primary_available = True
        self._consecutive_failures = 0

    def chat(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 200,
        temperature: float = 0.3,
    ) -> str:
        """Send a chat completion request, with automatic fallback.

        Returns the assistant's response text.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Try primary first
        if self._primary_available:
            try:
                result = self._call(
                    url=self.primary_url,
                    model=self.primary_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=None,
                    timeout=DEFAULT_TIMEOUT,
                )
                self._consecutive_failures = 0
                return result
            except Exception as e:
                self._consecutive_failures += 1
                logger.warning(
                    "Primary inference failed (%d consecutive): %s",
                    self._consecutive_failures, e,
                )
                # After 3 consecutive failures, stop trying primary for a while
                if self._consecutive_failures >= 3:
                    self._primary_available = False
                    logger.warning("Disabling primary endpoint after %d failures", self._consecutive_failures)

        # Fallback
        if not self.fallback_key:
            raise RuntimeError("Primary inference failed and no fallback API key configured")

        try:
            result = self._call(
                url=self.fallback_url,
                model=self.fallback_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                api_key=self.fallback_key,
                timeout=FALLBACK_TIMEOUT,
            )
            return result
        except Exception as e:
            logger.error("Fallback inference also failed: %s", e)
            raise

    def check_primary(self) -> bool:
        """Re-check if primary endpoint is reachable. Call periodically."""
        try:
            self._call(
                url=self.primary_url,
                model=self.primary_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
                api_key=None,
                timeout=5,
            )
            if not self._primary_available:
                logger.info("Primary endpoint is back online")
            self._primary_available = True
            self._consecutive_failures = 0
            return True
        except Exception:
            return False

    @staticmethod
    def _call(
        url: str,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        api_key: str | None,
        timeout: int,
    ) -> str:
        """Make an OpenAI-compatible chat completion request."""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode()

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())

        return data["choices"][0]["message"]["content"]
