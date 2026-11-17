#!/usr/bin/env python3
"""A stand-in for skmemory-mcp: MCP stdio, a few tools, every call logged as JSON lines.

FAKE_MCP_LOG: where calls go. FAKE_MCP_MODE: "" (normal), "hang" (never answers tools/call),
"die" (exits on tools/call). The child env is logged on initialize.
"""
import json
import os
import sys
import time

LOG = os.environ["FAKE_MCP_LOG"]
MODE = os.environ.get("FAKE_MCP_MODE", "")
STORE = []


def log(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def text(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


for line in sys.stdin:
    msg = json.loads(line)
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        log({"init": True, "env": {k: v for k, v in os.environ.items()
                                   if k.startswith(("SK", "FAKE"))}})
        reply(rid, {"protocolVersion": params.get("protocolVersion"), "capabilities": {},
                    "serverInfo": {"name": "fake-skmemory", "version": "0"}})
        continue
    if method != "tools/call":
        continue
    name, args = params.get("name"), params.get("arguments") or {}
    log({"tool": name, "args": args})
    if MODE == "hang":
        time.sleep(60)
    if MODE == "die":
        sys.exit(3)
    if name == "memory_store":
        STORE.append(args)
        reply(rid, text({"memory_id": f"id-{len(STORE)}", "stored": True}))
    elif name == "memory_search":
        reply(rid, text([{"id": "m1", "title": "Pet", "content": "The dog is Biscuit.", "tags": ["hermes"]}]))
    elif name == "memory_auto_context":
        reply(rid, text({"results": [{"id": "m1", "title": "Pet", "summary": "The dog is Biscuit."}]}))
    else:
        reply(rid, text({"error": f"unknown tool {name}"}))
