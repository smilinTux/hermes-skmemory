"""End to end against the real skmemory-mcp in isolated mode. Skipped when skmemory is not installed."""

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.test_provider import _load

REAL = os.environ.get("SKMEMORY_MCP_BIN") or shutil.which("skmemory-mcp") or (
    str(Path("~/.skenv/bin/skmemory-mcp").expanduser())
    if Path("~/.skenv/bin/skmemory-mcp").expanduser().exists() else None)

pytestmark = pytest.mark.skipif(not REAL, reason="skmemory-mcp not installed")


def test_isolated_store_recall_and_sensitive_filter(monkeypatch, tmp_path):
    monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "skmemory.json").write_text(json.dumps({"mcp_command": REAL, "agent": "itest", "timeout": 60}))
    p = _load(monkeypatch).SkmemoryProvider()
    p.initialize("s", hermes_home=str(home))
    try:
        stored = json.loads(p.handle_tool_call("skmemory_store", {"title": "pet", "content": "The dog is called Biscuit."}))
        assert stored["stored"] is True
        json.loads(p.handle_tool_call("skmemory_store", {"title": "rx", "content": "Biscuit medication schedule",
                                                         "tags": ["medical"]}))
        assert "Biscuit" in p.prefetch("what is the dog called?")
        assert "medication" not in p.prefetch("Biscuit medication schedule")
        found = json.loads(p.handle_tool_call("skmemory_search", {"query": "Biscuit"}))["results"]
        assert any("Biscuit" in r["content"] for r in found)
    finally:
        p.shutdown()
    assert list((home / ".skmemory" / "agents" / "itest" / "memory").rglob("*.json"))
