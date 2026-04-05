"""MCP tools: fetch from servers, convert to OpenAI format, execute tool calls."""

import json
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)


def _mcp_tool_to_openai(tool: Any) -> dict:
    """Convert MCP tool to OpenAI/Ollama function-calling format."""
    name = getattr(tool, "name", "") or ""
    desc = getattr(tool, "description", "") or f"Tool: {name}"
    schema = getattr(tool, "inputSchema", None) or {}
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": schema,
        },
    }


def _extract_text_from_result(result: Any) -> str:
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


def _has_mcp_tools(config: Any) -> bool:
    """Check if any MCP tool servers are configured."""
    servers = config.get_setting("mcpServers") or {}
    url = config.get_setting("mcp_tools_url")
    return (isinstance(servers, dict) and len(servers) > 0) or (url and isinstance(url, str) and url.strip())


@asynccontextmanager
async def mcp_connections(config: Any):
    """
    Connect to MCP servers, yield (ollama_tools, tool_to_server, call_tool).
    Uses: async with stdio_client(params) as (read, write), ClientSession(read, write).
    """
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    connections: dict[str, Any] = {}  # server_name -> session
    tool_to_server: dict[str, str] = {}

    async with AsyncExitStack() as stack:
        # Connect stdio servers
        servers = config.get_setting("mcpServers") or {}
        if isinstance(servers, dict):
            for name, cfg in servers.items():
                if not isinstance(cfg, dict):
                    continue
                cmd = cfg.get("command")
                args = cfg.get("args")
                if not cmd:
                    continue
                args = args if isinstance(args, list) else [str(a) for a in args]
                env_cfg = cfg.get("env")
                if isinstance(env_cfg, dict) and env_cfg:
                    merged_env = {
                        **os.environ,
                        **{str(k): str(v) for k, v in env_cfg.items()},
                    }
                    params = StdioServerParameters(command=str(cmd), args=args, env=merged_env)
                else:
                    params = StdioServerParameters(command=str(cmd), args=args)
                try:
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = ClientSession(read, write)
                    await stack.enter_async_context(session)
                    await session.initialize()
                    connections[name] = session
                    log.info("MCP connected to stdio server '%s'", name)
                except Exception as e:
                    log.warning("MCP stdio server '%s' failed: %s", name, e)

        # Connect SSE server
        url = config.get_setting("mcp_tools_url")
        if url and isinstance(url, str) and url.strip():
            try:
                from mcp.client.sse import sse_client

                read, write = await stack.enter_async_context(sse_client(url.strip()))
                session = ClientSession(read, write)
                await stack.enter_async_context(session)
                await session.initialize()
                connections["sse"] = session
                log.info("MCP connected to SSE server at %s", url)
            except ImportError:
                log.warning("MCP SSE client not available")
            except Exception as e:
                log.warning("MCP SSE server failed: %s", e)

        async def fetch_tools() -> tuple[list[dict], dict[str, str]]:
            ollama_tools: list[dict] = []
            for server_name, session in connections.items():
                try:
                    result = await session.list_tools()
                    tools = getattr(result, "tools", []) or []
                    for t in tools:
                        name = getattr(t, "name", "")
                        if name:
                            ollama_tools.append(_mcp_tool_to_openai(t))
                            tool_to_server[name] = server_name
                    log.info("MCP server '%s': %d tools", server_name, len(tools))
                except Exception as e:
                    log.warning("MCP list_tools failed for '%s': %s", server_name, e)
            return (ollama_tools, tool_to_server)

        async def call_tool(server_name: str, tool_name: str, arguments: dict[str, Any] | None) -> str:
            if server_name not in connections:
                return f"Error: server '{server_name}' not connected"
            session = connections[server_name]
            args = arguments if isinstance(arguments, dict) else {}
            try:
                result = await session.call_tool(tool_name, args)
                return _extract_text_from_result(result)
            except Exception as e:
                log.warning("MCP call_tool %s failed: %s", tool_name, e)
                return f"Error: {e}"

        ollama_tools, tool_to_server = await fetch_tools()
        yield (ollama_tools, tool_to_server, call_tool)
