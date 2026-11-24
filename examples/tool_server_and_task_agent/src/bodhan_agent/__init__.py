"""Bodhan task agent: plan, act, verify, retry, stop.

CLI entry point: ``python -m bodhan_agent``.
"""

from .agent import Agent, AgentConfig

__all__ = ["Agent", "AgentConfig"]
