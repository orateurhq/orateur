"""MCP client LLM backend - connects to LLM MCP servers."""

import asyncio
import json
import sys
from typing import Optional

from .base import LLMBackend


def _log(msg: str) -> None:
    print(f"[LLM/MCP] {msg}", file=sys.stderr, flush=True)


class MCPLLMBackend(LLMBackend):
    """LLM via MCP - connects to MCP servers exposing llm_generate/llm_chat tool."""

    def __init__(self, config):
        self.config = config
        self.ready = False

    def initialize(self, config) -> bool:
        self.config = config
        transport = config.get_setting("llm_mcp_transport", "stdio")
        command = config.get_setting("llm_mcp_command")
        url = config.get_setting("llm_mcp_url")
        if transport == "stdio" and not command:
            _log("llm_mcp_command required for stdio transport")
            return False
        if transport in ("sse", "streamable-http") and not url:
            _log("llm_mcp_url required for sse/http transport")
            return False
        self.ready = True
        _log("MCP LLM backend ready")
        return True

    def generate(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        if not self.ready or not user_text or not user_text.strip():
            return ""

        return asyncio.run(
            self._generate_async(user_text, system_prompt, model_override)
        )

    async def _generate_async(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        transport = self.config.get_setting("llm_mcp_transport", "stdio")
        tool = self.config.get_setting("llm_mcp_tool", "llm_generate")
        model = model_override or self.config.get_setting("llm_model")

        messages = [{"role": "user", "content": user_text}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        arguments = {"messages": messages}
        if system_prompt:
            arguments["system_prompt"] = system_prompt
        if model:
            arguments["model"] = model

        if transport == "stdio":
            command = self.config.get_setting("llm_mcp_command")
            if not command or not isinstance(command, list):
                _log("llm_mcp_command must be a list, e.g. ['uvx', 'my-llm-server']")
                return ""
            params = StdioServerParameters(command=command[0], args=command[1:])
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, arguments)
                        return self._extract_text(result)
            except Exception as e:
                _log(f"MCP stdio failed: {e}")
                return ""

        if transport in ("sse", "streamable-http"):
            url = self.config.get_setting("llm_mcp_url")
            if not url:
                return ""
            try:
                from mcp.client.sse import sse_client
                async with sse_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, arguments)
                        return self._extract_text(result)
            except ImportError:
                _log("MCP SSE client not available")
                return ""
            except Exception as e:
                _log(f"MCP SSE failed: {e}")
                return ""

        _log(f"Unknown transport: {transport}")
        return ""

    def _extract_text(self, result) -> str:
        """Extract text from MCP CallToolResult."""
        if getattr(result, "isError", False):
            return ""
        content = getattr(result, "content", []) or []
        texts = []
        for block in content:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif isinstance(block, dict) and "text" in block:
                texts.append(block["text"])
        out = " ".join(texts).strip()
        if not out and hasattr(result, "structuredContent") and result.structuredContent:
            sc = result.structuredContent
            if isinstance(sc, dict) and "content" in sc:
                return str(sc["content"]).strip()
            return json.dumps(sc)
        return out

    def is_ready(self) -> bool:
        return self.ready

    def get_available_models(self) -> list[str]:
        return []
