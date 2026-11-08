"""Stub Hermes core's ``agent.memory_provider`` so pytest can import the root __init__.py."""

import sys
import types

if "agent.memory_provider" not in sys.modules:
    _agent = types.ModuleType("agent")
    _mp = types.ModuleType("agent.memory_provider")
    _mp.MemoryProvider = type("MemoryProvider", (), {})
    sys.modules.setdefault("agent", _agent)
    sys.modules["agent.memory_provider"] = _mp
