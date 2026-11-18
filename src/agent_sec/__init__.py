"""Sandboxed testbed for LLM agent security.

A target agent calls simulated MCP tools. Attacks are poisoned tool
descriptions and multi-turn social engineering. Every tool call and outgoing
message is meant to pass through a policy gateway before it takes effect.
"""

__version__ = "0.1.0"
