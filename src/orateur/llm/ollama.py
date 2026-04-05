"""Ollama LLM backend."""

import asyncio
import json
import logging
from typing import Any, Optional

from .base import LLMBackend
from .mcp_tools import _has_mcp_tools, mcp_connections

log = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 10


def _parse_tool_arguments(arg: Any) -> dict[str, Any]:
    """Parse tool arguments from Ollama response (may be dict or JSON string)."""
    if isinstance(arg, dict):
        return arg
    if isinstance(arg, str):
        try:
            return json.loads(arg)
        except json.JSONDecodeError:
            return {}
    return {}


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert Ollama message to dict for appending to messages list."""
    d: dict[str, Any] = {"role": getattr(msg, "role", "assistant")}
    if hasattr(msg, "content") and msg.content:
        d["content"] = msg.content
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        d["tool_calls"] = []
        for tc in msg.tool_calls:
            fn = getattr(tc, "function", None)
            if fn:
                d["tool_calls"].append(
                    {
                        "type": "function",
                        "function": {
                            "name": getattr(fn, "name", ""),
                            "arguments": getattr(fn, "arguments", {}),
                        },
                    }
                )
    return d


class OllamaBackend(LLMBackend):
    """Ollama local LLM with optional MCP tools."""

    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.ready = False

    def initialize(self, config) -> bool:
        self.config = config
        try:
            import ollama

            ollama.list()
            self.ready = True
            log.info("Ollama ready")
            return True
        except ImportError as e:
            log.warning("ollama not installed: %s", e)
            return False
        except Exception as e:
            log.warning("Ollama init failed (is ollama running?): %s", e)
            return False

    def generate(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        if not self.ready or not user_text or not user_text.strip():
            return ""

        if _has_mcp_tools(self.config):
            try:
                return asyncio.run(self._generate_with_tools(user_text, system_prompt, model_override))
            except Exception as e:
                log.warning("MCP tools failed: %s", e)
        return self._generate_simple(user_text, system_prompt, model_override)

    def _generate_simple(
        self,
        user_text: str,
        system_prompt: Optional[str] | None,
        model_override: Optional[str] | None,
    ) -> str:
        """Simple generation without tools."""
        import ollama

        model = model_override or self.config.get_setting("llm_model", "llama3.2")
        base_url = self.config.get_setting("llm_base_url", "http://localhost:11434")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        try:
            client = ollama.Client(host=base_url)
            response = client.chat(model=model, messages=messages)
            if hasattr(response, "message") and response.message:
                return (response.message.content or "").strip()
            return ""
        except Exception as e:
            log.warning("Ollama generate failed: %s", e)
            return ""

    async def _generate_with_tools(
        self,
        user_text: str,
        system_prompt: Optional[str] | None,
        model_override: Optional[str] | None,
    ) -> str:
        """Generation with MCP tools. Connect, fetch tools, tool-call loop, disconnect."""
        import ollama

        model = model_override or self.config.get_setting("llm_model", "llama3.2")
        base_url = self.config.get_setting("llm_base_url", "http://localhost:11434")

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        async with mcp_connections(self.config) as (ollama_tools, tool_to_server, call_tool):
            if not ollama_tools:
                log.info("MCP no tools available, falling back to simple generation")
                return self._generate_simple(user_text, system_prompt, model_override)

            log.info("MCP passing %d tools to Ollama", len(ollama_tools))
            client = ollama.Client(host=base_url)

            for _ in range(_MAX_TOOL_ROUNDS):
                try:
                    response = client.chat(
                        model=model,
                        messages=messages,
                        tools=ollama_tools,
                    )
                except Exception as e:
                    log.warning("Ollama chat failed: %s", e)
                    return ""

                msg = getattr(response, "message", None)
                if not msg:
                    return ""

                tool_calls = getattr(msg, "tool_calls", None) or []
                if not tool_calls:
                    return (getattr(msg, "content", "") or "").strip()

                messages.append(_message_to_dict(msg))

                for tc in tool_calls:
                    fn = getattr(tc, "function", None)
                    if not fn:
                        continue
                    name = getattr(fn, "name", "")
                    args = _parse_tool_arguments(getattr(fn, "arguments", {}))
                    server = tool_to_server.get(name, "")
                    if not server:
                        result = f"Error: tool '{name}' not found"
                    else:
                        result = await call_tool(server, name, args)
                        log.info("MCP tool %s returned %d chars", name, len(str(result)))

                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": str(result),
                        }
                    )

        log.warning("MCP tool-call loop hit max rounds (%d)", _MAX_TOOL_ROUNDS)
        return ""

    def is_ready(self) -> bool:
        return self.ready

    def get_available_models(self) -> list[str]:
        try:
            import ollama

            base_url = self.config.get_setting("llm_base_url", "http://localhost:11434")
            client = ollama.Client(host=base_url)
            resp = client.list()
            if hasattr(resp, "models") and resp.models:
                return [m.model for m in resp.models if m.model is not None]
            return []
        except Exception:
            return []
