"""High-level MCP Client Hub for the MacroTrack Agent.

Connects to one or multiple local MCP servers (e.g. Nutrition and Fitness),
discovers their tools dynamically, converts them to OpenAI/Gemini function definitions,
and executes tool requests through the MCP protocol while enforcing policy safeguards.
"""

from __future__ import annotations
import anyio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer
from mcp.types import Tool as MCPTool

from agent_project.policy import execute_tool_with_policy, is_tool_allowed


class MCPClientHub:
    """Manages connections to multiple MCP servers and aggregates their tools."""

    def __init__(self) -> None:
        # server_name -> MCPServer object or StdioServerParameters
        self._servers: Dict[str, Any] = {}
        # tool_name -> server_name
        self._tool_to_server: Dict[str, str] = {}
        # cached tool definitions in OpenAI format
        self._openai_tools: List[Dict[str, Any]] = []

    def register_server(self, name: str, server: MCPServer | StdioServerParameters) -> None:
        """Register an MCP server instance or subprocess specification."""
        self._servers[name] = server

    async def initialize_tools(self) -> List[Dict[str, Any]]:
        """Connect to all registered servers, discover tools, and convert to OpenAI schemas."""
        self._tool_to_server.clear()
        self._openai_tools.clear()

        for srv_name, srv in self._servers.items():
            async with Client(srv) as client:
                res = await client.list_tools()
                for tool in res.tools:
                    self._tool_to_server[tool.name] = srv_name
                    self._openai_tools.append(self._convert_mcp_to_openai(tool))

        return self._openai_tools

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return the aggregated OpenAI function definitions for all registered MCP tools."""
        return self._openai_tools

    async def call_tool(
        self,
        tool_name: str,
        arguments_json: str,
        confirm_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    ) -> Dict[str, Any]:
        """Dispatch a tool call.

        If the tool is a core registered local tool with policy rules,
        it evaluates security policies and human confirmation gates before delegating.
        """
        # First check security policy
        # If the tool is managed by our local policy enforcement:
        if is_tool_allowed(tool_name):
            return execute_tool_with_policy(
                tool_name=tool_name,
                raw_args_str=arguments_json,
                confirm_handler=confirm_handler,
            )

        # Otherwise, check if it belongs to an MCP server (e.g. fitness server)
        srv_name = self._tool_to_server.get(tool_name)
        if not srv_name:
            return {
                "status": "error",
                "message": f"Narzędzie '{tool_name}' nie zostało znalezione na żadnym podłączonym serwerze MCP.",
            }

        srv = self._servers[srv_name]
        try:
            args_dict = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as err:
            return {"status": "error", "message": f"Niepoprawny JSON argumentów: {err}"}

        async with Client(srv) as client:
            call_res = await client.call_tool(tool_name, args_dict)
            parsed_blocks = []
            for block in call_res.content:
                if hasattr(block, "text"):
                    try:
                        parsed_blocks.append(json.loads(block.text))
                    except Exception:
                        parsed_blocks.append(block.text)

            if not parsed_blocks:
                return {"status": "success", "result": None}
            if len(parsed_blocks) == 1:
                return {"status": "success", "result": parsed_blocks[0]}
            return {"status": "success", "result": parsed_blocks}

    @staticmethod
    def _convert_mcp_to_openai(tool: MCPTool) -> Dict[str, Any]:
        """Transform an MCP tool definition into OpenAI function calling format."""
        schema = tool.input_schema or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or f"Narzędzie {tool.name}",
                "parameters": schema,
            },
        }


def create_default_mcp_hub() -> MCPClientHub:
    """Create a standard Hub preconfigured with both Nutrition and Fitness servers."""
    from agent_project.mcp_server import mcp as nutrition_server
    from agent_project.fitness_mcp_server import mcp as fitness_server

    hub = MCPClientHub()
    hub.register_server("nutrition", nutrition_server)
    hub.register_server("fitness", fitness_server)
    return hub
