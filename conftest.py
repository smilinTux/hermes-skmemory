"""Make the plugin importable under pytest with or without Hermes core installed.

The root ``__init__.py`` does ``from agent.memory_provider import MemoryProvider``.
pytest may import that package before any test runs, so the stub has to be in place
here, and it must cope with a missing ``agent`` package, an ``agent`` without
``memory_provider``, or a real Hermes core (which is left alone).
"""

import sys
import types

try:
    from agent.memory_provider import MemoryProvider  # noqa: F401  (real Hermes core)
except Exception:  # ImportError, or any import-time failure of a partial install
    _agent = types.ModuleType("agent")
    _agent.__path__ = []  # behave as a package so submodule imports resolve
    _mp = types.ModuleType("agent.memory_provider")
    _mp.MemoryProvider = type("MemoryProvider", (), {})
    _agent.memory_provider = _mp
    sys.modules["agent"] = _agent
    sys.modules["agent.memory_provider"] = _mp
