"""Per-chat scope tests (SKMEM-P0-23). Hermes core is stubbed; skmemory is stubbed where needed."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(monkeypatch, reader="OWNER", sens=True):
    agent = types.ModuleType("agent")
    mp = types.ModuleType("agent.memory_provider")
    mp.MemoryProvider = type("MemoryProvider", (), {})
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", mp)
    if reader != "MISSING":
        tools = types.ModuleType("tools")
        ss = types.ModuleType("tools.session_scope")
        ss.session_reader = (lambda: None) if reader == "OWNER" else (lambda: reader)
        monkeypatch.setitem(sys.modules, "tools", tools)
        monkeypatch.setitem(sys.modules, "tools.session_scope", ss)
    else:
        monkeypatch.setitem(sys.modules, "tools", None)
        monkeypatch.setitem(sys.modules, "tools.session_scope", None)
    if sens:
        for name in ("skmemory", "skmemory.hooks"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        sm = types.ModuleType("skmemory.hooks.sensitivity")
        sm.is_sensitive = lambda tags: "sacred" in (tags or [])
        sm.is_owner_only = lambda tags: "owner-only" in (tags or [])
        monkeypatch.setitem(sys.modules, "skmemory.hooks.sensitivity", sm)
    spec = importlib.util.spec_from_file_location("hs_plugin", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class M:
    def __init__(self, title, tags=()):
        self.title, self.content, self.id, self.tags = title, "c", title, list(tags)


class Store:
    def __init__(self, with_reader=True):
        self.calls, self.with_reader = [], with_reader

    def search(self, query, limit=10, **kw):
        if "reader" in kw and not self.with_reader:
            raise TypeError("unexpected kwarg reader")
        self.calls.append(kw)
        return [M("ok"), M("sac", ["sacred"]), M("own", ["owner-only"])]


def _prov(mod, store):
    p = mod.SkmemoryProvider()
    p.store = store
    return p


def test_owner_prefetch_passes_no_reader_and_drops_sensitive(monkeypatch):
    mod = _load(monkeypatch)
    st = Store()
    out = _prov(mod, st).prefetch("q")
    assert st.calls == [{}]
    assert "ok" in out and "sac" not in out and "own" not in out


def test_group_chat_prefetch_uses_channel_reader(monkeypatch):
    mod = _load(monkeypatch, reader="channel:42")
    st = Store()
    out = _prov(mod, st).prefetch("q")
    assert st.calls == [{"reader": "channel:42"}]
    assert "sac" not in out


def test_tool_search_scoped_and_filtered_for_non_owner(monkeypatch):
    mod = _load(monkeypatch, reader="@public")
    st = Store()
    out = _prov(mod, st).handle_tool_call("skmemory_search", {"query": "x"})
    assert st.calls == [{"reader": "@public"}]
    assert "sac" not in out and "own" not in out


def test_old_skmemory_without_reader_fails_closed(monkeypatch):
    mod = _load(monkeypatch, reader="channel:1")
    assert _prov(mod, Store(with_reader=False)).prefetch("q") == ""


def test_no_sensitivity_module_injects_nothing(monkeypatch):
    mod = _load(monkeypatch, sens=False)
    monkeypatch.setitem(sys.modules, "skmemory", None)
    assert _prov(mod, Store()).prefetch("q") == ""


@pytest.mark.parametrize("gateway,expected", [("1", "@public"), ("", None)])
def test_missing_session_scope(monkeypatch, gateway, expected):
    mod = _load(monkeypatch, reader="MISSING")
    monkeypatch.setenv("_HERMES_GATEWAY", gateway)
    assert mod._session_reader() == expected


# -- write side: sync_turn and skmemory_store ---------------------------------


class WStore:
    def __init__(self):
        self.writes = []

    def snapshot(self, **kw):
        self.writes.append(kw)
        return types.SimpleNamespace(id="m1")


def _wprov(mod):
    p = _prov(mod, WStore())
    p._writable = True
    p._read_only = False
    return p


def test_owner_sync_turn_writes(monkeypatch):
    mod = _load(monkeypatch)
    p = _wprov(mod)
    p.sync_turn("hi", "hello", session_id="s1")
    assert len(p.store.writes) == 1


@pytest.mark.parametrize("reader", ["@public", "channel:42"])
def test_non_owner_sync_turn_writes_nothing(monkeypatch, reader):
    monkeypatch.delenv("HERMES_SKMEMORY_WRITE_NONOWNER", raising=False)
    mod = _load(monkeypatch, reader=reader)
    p = _wprov(mod)
    p.sync_turn("hi", "hello", session_id="s1")
    assert p.store.writes == []


def test_non_owner_store_tool_writes_nothing(monkeypatch):
    mod = _load(monkeypatch, reader="channel:42")
    p = _wprov(mod)
    out = p.handle_tool_call("skmemory_store", {"content": "x"})
    assert p.store.writes == [] and "error" in out.lower()


def test_owner_store_tool_writes(monkeypatch):
    mod = _load(monkeypatch)
    p = _wprov(mod)
    p.handle_tool_call("skmemory_store", {"content": "x"})
    assert len(p.store.writes) == 1


def test_scope_failure_writes_nothing(monkeypatch):
    mod = _load(monkeypatch)
    monkeypatch.setattr(
        mod, "_session_reader", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    p = _wprov(mod)
    p.sync_turn("hi", "hello")
    p.handle_tool_call("skmemory_store", {"content": "x"})
    assert p.store.writes == []


def test_missing_scope_in_gateway_writes_nothing(monkeypatch):
    mod = _load(monkeypatch, reader="MISSING")
    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    p = _wprov(mod)
    p.sync_turn("hi", "hello")
    assert p.store.writes == []


def test_optin_env_does_not_write_without_audience_support(monkeypatch):
    # skmemory's snapshot() has no audience field, so the opt-in does not exist:
    # the env var must not re-open the owner store to a group chat.
    monkeypatch.setenv("HERMES_SKMEMORY_WRITE_NONOWNER", "1")
    mod = _load(monkeypatch, reader="channel:42")
    p = _wprov(mod)
    p.sync_turn("hi", "hello")
    assert p.store.writes == []
