"""MCP client LLM backend - connects to MCP servers."""

import asyncio
import json
import logging
from typing import Optional

from .base import LLMBackend

log = logging.getLogger(__name__)


class MCPLLMBackend(LLMBackend):
    """
    LLM via MCP - connects to all servers in mcpServers.
    Finds the one exposing llm_generate/llm_chat for generation,
    and aggregates tools from all servers for the LLM to use.
    """

    def __init__(self, config):
        self.config = config
        self.ready = False

    def initialize(self, config) -> bool:
        self.config = config
        servers = config.get_setting("mcpServers") or {}
        if not servers or not isinstance(servers, dict):
            log.warning("mcpServers required (Cursor-style: { name: { command, args } })")
            return False
        transport = config.get_setting("llm_mcp_transport", "stdio")
        tool_name = config.get_setting("llm_mcp_tool", "llm_generate")
        log.info("MCP config: transport=%s, tool=%s, servers=%s", transport, tool_name, list(servers.keys()))
        if transport in ("sse", "streamable-http"):
            url = config.get_setting("llm_mcp_url")
            if not url:
                log.warning("llm_mcp_url required for sse/http transport")
                return False
        self.ready = True
        log.info("MCP LLM backend ready")
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

    async def _discover_servers(
        self,
    ) -> tuple[str | None, list[dict], dict[str, tuple[str, list[str]]]]:
        """
        Connect to all mcpServers, list tools, find which has llm_generate.
        Returns (llm_server_name, aggregated_tools, server_commands).
        """
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        servers = self.config.get_setting("mcpServers") or {}
        if not isinstance(servers, dict):
            return (None, [], {})

        server_commands: dict[str, tuple[str, list[str]]] = {}
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            cmd = cfg.get("command")
            args = cfg.get("args")
            if not cmd:
                continue
            args = args if isinstance(args, list) else []
            server_commands[name] = (str(cmd), [str(a) for a in args])

        llm_server: str | None = None
        all_tools: list[dict] = []
        skip_tools = {"llm_generate", "llm_chat"}

        async def fetch_server(name: str, cmd: str, args: list[str]) -> None:
            nonlocal llm_server
            params = StdioServerParameters(command=cmd, args=args)
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        tools = getattr(tools_result, "tools", []) or []
                        tool_names = [getattr(t, 'name', '?') for t in tools]
                        log.info("MCP server '%s': %d tools -> %s", name, len(tools), tool_names)
                        for t in tools:
                            tname = getattr(t, "name", "")
                            if tname in skip_tools:
                                if llm_server is None:
                                    llm_server = name
                                    log.info("LLM server: '%s' (has %s)", name, tname)
                            else:
                                all_tools.append({
                                    "name": tname,
                                    "description": getattr(t, "description", "") or "",
                                    "inputSchema": getattr(t, "inputSchema", {}) or {},
                                })
            except Exception as e:
                log.warning("MCP server '%s' failed: %s", name, e)

        if server_commands:
            log.info("MCP discovering %d servers: %s", len(server_commands), list(server_commands.keys()))
            await asyncio.gather(*[
                fetch_server(n, c, a) for n, (c, a) in server_commands.items()
            ])
            log.info("MCP aggregated %d tools: %s", len(all_tools), [t['name'] for t in all_tools])

        return (llm_server, all_tools, server_commands)

    async def _generate_async(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        transport = self.config.get_setting("llm_mcp_transport", "stdio")
        tool_name = self.config.get_setting("llm_mcp_tool", "llm_generate")
        model = model_override or self.config.get_setting("llm_model")

        messages = [{"role": "user", "content": user_text}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        arguments = {"messages": messages}
        if system_prompt:
            arguments["system_prompt"] = system_prompt
        if model:
            arguments["model"] = model

        llm_server, aggregated_tools, server_commands = await self._discover_servers()
        if not llm_server:
            log.warning("No MCP server in mcpServers exposes llm_generate or llm_chat")
            return ""
        log.info("MCP using LLM server '%s' for generation", llm_server)
        if aggregated_tools:
            arguments["tools"] = aggregated_tools
            log.info("MCP passing %d tools to %s: %s", len(aggregated_tools), tool_name, [t['name'] for t in aggregated_tools])
        else:
            log.info("MCP no tools to pass (LLM-only or tool servers returned none)")

        cmd, args = server_commands[llm_server]
        log.info("MCP calling %s on server '%s' (%s %s)", tool_name, llm_server, cmd, ' '.join(args))

        if transport == "stdio":
            params = StdioServerParameters(command=cmd, args=args)
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        log.info("MCP calling %s with %d chars user text", tool_name, len(user_text))
                        result = await session.call_tool(tool_name, arguments)
                        out = self._extract_text(result)
                        log.info("MCP %s returned %d chars", tool_name, len(out))
                        return out
            except Exception as e:
                log.warning("MCP stdio failed: %s", e)
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
                        log.info("MCP calling %s (SSE) with %d chars user text", tool_name, len(user_text))
                        result = await session.call_tool(tool_name, arguments)
                        out = self._extract_text(result)
                        log.info("MCP %s returned %d chars", tool_name, len(out))
                        return out
            except ImportError:
                log.warning("MCP SSE client not available")
                return ""
            except Exception as e:
                log.warning("MCP SSE failed: %s", e)
                return ""

        log.warning("Unknown transport: %s", transport)
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
