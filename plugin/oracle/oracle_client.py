"""Oracle client — queries the always-warm local 7B model.

The oracle holds all session notes + KG + seeds in its system prompt.
MLX caches the system prompt KV, so subsequent queries are fast (~1-3s).
"""

import json
import logging
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".memorable" / "data"
CONTEXT_PATH = DATA_DIR / "oracle-context.md"

DEFAULT_URL = "http://192.168.68.58:8400/v1/chat/completions"
DEFAULT_MODEL = "mlx-community/Qwen2.5-7B-Instruct-1M-4bit"
DEFAULT_TIMEOUT = 300  # 5 min — first query processes full context


class OracleClient:
    """HTTP client for the memory oracle (MLX server on Mac Mini)."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.url = url
        self.model = model
        self.timeout = timeout
        self._context: str | None = None

    def ask(
        self,
        question: str,
        max_tokens: int = 500,
        temperature: float = 0.1,
        context_override: str | None = None,
    ) -> str:
        """Ask the oracle a question about past context.

        Args:
            question: The question to ask.
            max_tokens: Max response tokens.
            temperature: Sampling temperature (low = factual).
            context_override: Use this context instead of the cached one.
                Used by rebuild_and_warm() to warm with fresh context.

        Returns:
            The oracle's response text.
        """
        context = context_override or self._get_context()
        if not context:
            return "Oracle context not available. Run: python3 -m oracle.build_context"

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": question},
        ]

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode()

        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            logger.error("Oracle unreachable at %s: %s", self.url, e)
            raise ConnectionError(f"Oracle unreachable at {self.url}: {e}") from e
        except (KeyError, json.JSONDecodeError) as e:
            logger.error("Oracle returned unexpected response: %s", e)
            raise RuntimeError(f"Oracle returned unexpected response: {e}") from e

    def _get_context(self) -> str:
        """Load oracle context from disk (cached in memory after first load)."""
        if self._context is not None:
            return self._context

        if not CONTEXT_PATH.exists():
            logger.warning("Oracle context not found at %s", CONTEXT_PATH)
            return ""

        try:
            self._context = CONTEXT_PATH.read_text()
            logger.info("Oracle context loaded: %d chars", len(self._context))
            return self._context
        except OSError as e:
            logger.error("Failed to read oracle context: %s", e)
            return ""

    def reload_context(self):
        """Force reload of context from disk (after rebuild)."""
        self._context = None
