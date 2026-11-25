"""Provider tests against a fake skmemory-mcp (tests/fake_skmemory_mcp.py). Hermes core is stubbed."""

import importlib.util
import json
import logging
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake_skmemory_mcp.py"
_SECRETS = {}


def _load(monkeypatch, reader="OWNER"):
    agent = types.ModuleType("agent")
    agent.__path__ = []
    mp = types.ModuleType("agent.memory_provider")
    mp.MemoryProvider = type("MemoryProvider", (), {})
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", mp)
    sc = types.ModuleType("agent.secret_scope")
    sc.get_secret = lambda name, default=None: _SECRETS.get(name, default)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", sc)
    if reader == "MISSING":
        monkeypatch.setitem(sys.modules, "tools", None)
        monkeypatch.setitem(sys.modules, "tools.session_scope", None)
    else:
        tools = types.ModuleType("tools")
        ss = types.ModuleType("tools.session_scope")
        if reader == "RAISES":
            def boom():
                raise RuntimeError("scope broken")
            ss.session_reader = boom
        else:
            ss.session_reader = (lambda: None) if reader == "OWNER" else (lambda: reader)
        monkeypatch.setitem(sys.modules, "tools", tools)
        monkeypatch.setitem(sys.modules, "tools.session_scope", ss)
    spec = importlib.util.spec_from_file_location("hs_plugin", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_MCP_LOG", str(log))
    monkeypatch.setenv("SKMEMORY_MCP_BIN", f"{sys.executable}")
    monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
    home = tmp_path / "hermes"
    home.mkdir()
    return home, log


def _provider(mod, home, extra_cfg=None, **kwargs):
    cfg = {"mcp_command": sys.executable, "timeout": 5}
    cfg.update(extra_cfg or {})
    (home / "skmemory.json").write_text(json.dumps(cfg))
    p = mod.SkmemoryProvider()
    p.initialize("sess1", hermes_home=str(home), **kwargs)
    # The fake server is a script, so run it with the interpreter. Its test knobs are
    # not on the child-env allowlist, so hand them over explicitly.
    if p.client is not None:
        p.client._argv = [sys.executable, str(FAKE)]
        p.client._env.update({k: v for k, v in os.environ.items() if k.startswith("FAKE_MCP_")})
    return p


def _calls(log, tool=None):
    if not log.exists():
        return []
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    return [r for r in rows if "tool" in r and (tool is None or r["tool"] == tool)]


def _init_env(log):
    return next(json.loads(line) for line in log.read_text().splitlines() if '"init"' in line)["env"]


# -- owner turns --------------------------------------------------------------


def test_owner_prefetch_uses_auto_context_without_reader(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home)
    out = p.prefetch("what is my dog called again?", session_id="s")
    assert "Biscuit" in out
    (call,) = _calls(log, "memory_auto_context")
    assert "reader" not in call["args"]
    assert "dog" in call["args"]["keywords"].split()
    p.shutdown()


def test_owner_sync_turn_writes_short_term(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home)
    p.sync_turn("my dog is Biscuit", "Noted!", session_id="s9")
    (call,) = _calls(log, "memory_store")
    assert call["args"]["layer"] == "short-term"
    assert call["args"]["tags"] == ["hermes", "session:s9"]
    assert "Biscuit" in call["args"]["content"]
    p.shutdown()


def test_owner_store_tool_writes(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home)
    res = json.loads(p.handle_tool_call("skmemory_store", {"content": "likes tea", "tags": ["pref"]}))
    assert res == {"stored": True, "id": "id-1"}
    assert _calls(log, "memory_store")[0]["args"]["tags"] == ["pref"]
    p.shutdown()


def test_search_tool_returns_trimmed_rows(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home)
    res = json.loads(p.handle_tool_call("skmemory_search", {"query": "dog", "limit": 99}))
    assert res["results"][0]["title"] == "Pet"
    assert _calls(log, "memory_search")[0]["args"]["limit"] == 20
    p.shutdown()


# -- non-owner turns ----------------------------------------------------------


@pytest.mark.parametrize("reader", ["channel:abc", "@public"])
def test_non_owner_reads_under_reader_and_never_writes(monkeypatch, env, reader):
    home, log = env
    p = _provider(_load(monkeypatch, reader=reader), home)
    p.prefetch("tell me about the dog", session_id="s")
    p.handle_tool_call("skmemory_search", {"query": "dog"})
    p.sync_turn("hi", "hello", session_id="s")
    res = json.loads(p.handle_tool_call("skmemory_store", {"content": "x"}))
    assert "error" in res
    assert _calls(log, "memory_store") == []
    assert _calls(log, "memory_auto_context")[0]["args"]["reader"] == reader
    assert _calls(log, "memory_search")[0]["args"]["reader"] == reader
    p.shutdown()


def test_scope_error_is_public_and_writes_nothing(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch, reader="RAISES"), home)
    p.sync_turn("hi", "hello")
    p.prefetch("dog question here")
    assert _calls(log, "memory_store") == []
    assert _calls(log, "memory_auto_context")[0]["args"]["reader"] == "@public"
    p.shutdown()


@pytest.mark.parametrize("gateway,assume,owner", [("1", False, False), ("", False, True), ("1", True, True)])
def test_missing_session_scope(monkeypatch, env, gateway, assume, owner):
    home, log = env
    if gateway:
        monkeypatch.setenv("_HERMES_GATEWAY", gateway)
    p = _provider(_load(monkeypatch, reader="MISSING"), home, {"assume_owner": assume})
    p.sync_turn("hi", "hello")
    assert bool(_calls(log, "memory_store")) is owner
    p.shutdown()


# -- modes and config ---------------------------------------------------------


def test_read_only_hides_store_and_refuses_writes(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home, {"read_only": True})
    assert [s["name"] for s in p.get_tool_schemas()] == ["skmemory_search"]
    assert "error" in json.loads(p.handle_tool_call("skmemory_store", {"content": "x"}))
    p.sync_turn("a", "b")
    assert _calls(log, "memory_store") == []


def test_non_primary_context_never_writes(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home, agent_context="cron")
    p.sync_turn("a", "b")
    assert _calls(log, "memory_store") == []
    p.shutdown()


def test_isolated_mode_child_env(monkeypatch, env):
    home, log = env
    monkeypatch.setenv("SKMEMORY_PG_DSN", "postgresql://someone-else")
    monkeypatch.setenv("SKCAPSTONE_HOME", "/somewhere/else")
    p = _provider(_load(monkeypatch), home, {"agent": "mybot"})
    p.prefetch("dog question")
    e = _init_env(log)
    assert e["SKMEMORY_HOME"] == str(home / ".skmemory" / "agents" / "mybot")
    assert e["SKCAPSTONE_HOME"] == str(home / ".skmemory")
    assert e["SKAGENT"] == e["SKMEMORY_AGENT"] == e["SKCAPSTONE_AGENT"] == "mybot"
    assert "SKMEMORY_PG_DSN" not in e
    assert (home / ".skmemory" / "agents" / "mybot" / "config" / "skmemory.yaml").exists()
    p.shutdown()


def test_linked_mode_child_env(monkeypatch, env, tmp_path):
    home, log = env
    sk = tmp_path / "skcap"
    (sk / "agents" / "lumina").mkdir(parents=True)
    monkeypatch.setitem(_SECRETS, "SKMEMORY_PG_DSN", "postgresql://node-local")
    monkeypatch.setenv("SKMEMORY_PG_DSN", "postgresql://from-process-env")
    p = _provider(_load(monkeypatch), home, {"agent": "lumina", "skcapstone_home": str(sk)})
    p.prefetch("dog question")
    e = _init_env(log)
    assert e["SKCAPSTONE_HOME"] == str(sk)
    assert "SKMEMORY_HOME" not in e
    assert e["SKMEMORY_PG_DSN"] == "postgresql://node-local"
    assert not (home / ".skmemory").exists()
    p.shutdown()


def test_no_server_means_unavailable(monkeypatch, tmp_path):
    mod = _load(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SKMEMORY_MCP_BIN", raising=False)
    p = mod.SkmemoryProvider()
    assert p.is_available() is False
    p.initialize("s", hermes_home=str(tmp_path))
    assert p.client is None
    assert p.prefetch("anything at all") == ""
    assert "error" in json.loads(p.handle_tool_call("skmemory_search", {"query": "x"}))


def test_save_config_keeps_known_keys(monkeypatch, tmp_path):
    p = _load(monkeypatch).SkmemoryProvider()
    p.save_config({"agent": "a", "read_only": True, "bogus": 1}, str(tmp_path))
    assert json.loads((tmp_path / "skmemory.json").read_text()) == {"agent": "a", "read_only": True}


def test_child_env_is_an_allowlist(monkeypatch, env):
    home, log = env
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("SOME_TOKEN", "t")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    p = _provider(_load(monkeypatch), home)
    p.prefetch("dog question")
    e = _init_env(log)
    assert not {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "SOME_TOKEN"} & set(e)
    assert e["LC_ALL"] == "C.UTF-8" and "PATH" in e
    p.shutdown()


def test_isolated_mode_never_gets_a_dsn(monkeypatch, env):
    home, log = env
    monkeypatch.setitem(_SECRETS, "SKMEMORY_PG_DSN", "postgresql://node-local")
    p = _provider(_load(monkeypatch), home)
    p.prefetch("dog question")
    assert "SKMEMORY_PG_DSN" not in _init_env(log)
    p.shutdown()


def test_dsn_in_config_file_is_ignored(monkeypatch, env, tmp_path, caplog):
    home, log = env
    sk = tmp_path / "skcap"
    (sk / "agents" / "a").mkdir(parents=True)
    with caplog.at_level(logging.WARNING):
        p = _provider(_load(monkeypatch), home,
                      {"agent": "a", "skcapstone_home": str(sk), "dsn": "postgresql://in-file"})
    p.prefetch("dog question")
    assert "SKMEMORY_PG_DSN" not in _init_env(log)
    assert "dsn" not in p._cfg
    assert "ignoring `dsn`" in caplog.text
    p.shutdown()


@pytest.mark.parametrize("version,warns", [("0.11.173", False), ("0.11.190", False),
                                           ("0.11.172", True), ("0.12.0", True), ("1.30.0", True)])
def test_untested_server_version_warns(monkeypatch, env, caplog, version, warns):
    home, log = env
    monkeypatch.setenv("FAKE_MCP_VERSION", version)
    p = _provider(_load(monkeypatch), home)
    with caplog.at_level(logging.WARNING):
        p.prefetch("dog question")
    assert ("is tested with skmemory" in caplog.text) is warns
    assert p.client.server_info["version"] == version
    p.shutdown()


# -- transport ----------------------------------------------------------------


def test_hung_server_times_out_with_error(monkeypatch, env):
    home, log = env
    monkeypatch.setenv("FAKE_MCP_MODE", "hang")
    p = _provider(_load(monkeypatch), home, {"timeout": 1})
    t = time.time()
    res = json.loads(p.handle_tool_call("skmemory_search", {"query": "dog"}))
    assert "timed out" in res["error"]
    assert time.time() - t < 5
    p.shutdown()


def test_dead_server_is_restarted_on_next_call(monkeypatch, env):
    home, log = env
    monkeypatch.setenv("FAKE_MCP_MODE", "die")
    p = _provider(_load(monkeypatch), home)
    assert "error" in json.loads(p.handle_tool_call("skmemory_search", {"query": "dog"}))
    monkeypatch.setenv("FAKE_MCP_MODE", "")
    p.client._env["FAKE_MCP_MODE"] = ""
    assert json.loads(p.handle_tool_call("skmemory_search", {"query": "dog"}))["results"]
    p.shutdown()


def test_queue_prefetch_result_is_consumed_once(monkeypatch, env):
    home, log = env
    p = _provider(_load(monkeypatch), home)
    p.queue_prefetch("what about the dog", session_id="s")
    for _ in range(50):
        if p._prefetched.get("s") is not None:
            break
        time.sleep(0.1)
    assert "Biscuit" in p.prefetch("ignored now", session_id="s")
    assert len(_calls(log, "memory_auto_context")) == 1
    p.prefetch("dog again please", session_id="s")
    assert len(_calls(log, "memory_auto_context")) == 2
    p.shutdown()


def test_keywords_drop_stopwords_and_short_words(monkeypatch):
    mod = _load(monkeypatch)
    kw = mod._keywords("Hey, what is the name of my dog? Thanks!").split()
    assert set(kw) == {"name", "dog"}
    assert mod._keywords("ok hi") == ""
