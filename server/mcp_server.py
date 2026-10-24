#!/usr/bin/env python3
"""Zero-dependency MCP server (stdio, newline-delimited JSON-RPC 2.0) for the dashboard plugin.

Tools only edit the dashboard CONFIG and run the plain build script. They never accept secret values:
sources reference environment variable NAMES. Dashboard folder: $DASHBOARD_HOME (default ~/.agent-dashboard).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dashlib import __version__  # noqa: E402
from dashlib import config as cfgmod  # noqa: E402
from dashlib.build import LeakError, build  # noqa: E402
from dashlib.config import ConfigError  # noqa: E402
from dashlib.library import LIBRARY  # noqa: E402
from dashlib.opener import open_dashboard  # noqa: E402

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
MAX_TOOL_BYTES = 8 * 1024   # no tool answer is ever longer than this (summaries only; data and HTML are never returned)
MAX_ERR_CHARS = 420
MAX_ERR_CHARS_MANY = 140
SOURCE_SCHEMA = {
    "type": "object", "description": "Data source; default {\"mode\":\"sample\"}. Never put a key here, only an env var NAME in auth_env.",
    "properties": {"mode": {"type": "string", "enum": ["sample", "file", "http_json"]},
                   "path": {"type": "string", "description": "file: .json/.csv path inside the dashboard folder, e.g. data/sales.json"},
                   "url": {"type": "string", "description": "http_json: https URL (http only for localhost) returning the panel's raw JSON; no credentials in it"},
                   "auth_env": {"type": "string", "description": "NAME of the env var holding the token"},
                   "auth_header": {"type": "string", "description": "default Authorization"},
                   "auth_scheme": {"type": "string", "description": "default Bearer; \"\" for a raw value"},
                   "timeout": {"type": "number", "description": "total seconds for the request, 1-30, default 10"}},
    "required": ["mode"], "additionalProperties": False}
OPTIONS_SCHEMA = {"type": "object", "description": "Panel options (see list_panel_library)."}
STR = {"type": "string"}


def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}}


TOOLS = [
    _tool("setup", "First run in one call: creates the dashboard folder + sample panels, builds, opens the page. Safe to repeat (never overwrites; only adds missing panel types).",
          {"title": STR, "panels": {"type": "array", "items": {"type": "string", "enum": list(LIBRARY)}, "description": "panel types wanted; default all five"},
           "no_open": {"type": "boolean", "description": "build only, no browser"}}),
    _tool("init", "Low-level: create the config once (prefer setup).",
          {"title": STR, "panels": {"type": "array", "items": {"type": "string", "enum": list(LIBRARY)}}, "force": {"type": "boolean"}}),
    _tool("list_panel_library", "Panel types with options, raw data shape and how to connect real data. Pass type for one entry (much shorter).",
          {"type": {"type": "string", "enum": list(LIBRARY)}}),
    _tool("list_panels", "List the panels in display order."),
    _tool("add_panel", "Add a panel (sample data by default). Then call open_dashboard.",
          {"type": {"type": "string", "enum": list(LIBRARY)}, "id": {"type": "string", "description": "a-z0-9_-, max 40; default from type"},
           "title": STR, "source": SOURCE_SCHEMA, "options": OPTIONS_SCHEMA, "position": {"type": "integer", "description": "0-based; default end"}}, ["type"]),
    _tool("update_panel", "Change a panel's title, source (e.g. sample -> file) or options (merged).",
          {"id": STR, "title": STR, "source": SOURCE_SCHEMA, "options": OPTIONS_SCHEMA}, ["id"]),
    _tool("remove_panel", "Remove a panel by id.", {"id": STR}, ["id"]),
    _tool("reorder_panels", "Set the panel order; list every id exactly once.", {"order": {"type": "array", "items": STR}}, ["order"]),
    _tool("open_dashboard", "Rebuild (plain script, no LLM) and open the page in the default browser. For open/show/refresh. Returns the path and whether it opened.",
          {"no_open": {"type": "boolean", "description": "build only"}}),
    _tool("rebuild", "Rebuild out/dashboard.html and out/data.json without opening it."),
]


def _count_errors(o):
    if isinstance(o, dict):
        return sum(1 if k == "error" else _count_errors(v) for k, v in o.items())
    if isinstance(o, list):
        return sum(_count_errors(v) for v in o)
    return 0


def _short_errors(o, n=None):
    """Cut long per-panel error text so a bad source can't make the answer big.
    A few errors keep their full Fix: sentence; with many errors each is cut harder."""
    if n is None:
        n = MAX_ERR_CHARS if _count_errors(o) <= 5 else MAX_ERR_CHARS_MANY
    if isinstance(o, dict):
        return {k: (v[:n] if k == "error" and isinstance(v, str) else _short_errors(v, n)) for k, v in o.items()}
    if isinstance(o, list):
        return [_short_errors(v, n) for v in o]
    return o


def _text(obj, error=False):
    t = obj if isinstance(obj, str) else json.dumps(_short_errors(obj), separators=(",", ":"))
    b = t.encode("utf-8")
    if len(b) > MAX_TOOL_BYTES:
        t = b[:MAX_TOOL_BYTES - 80].decode("utf-8", "ignore") + "...[answer cut at %d KB; %d bytes omitted]" % (MAX_TOOL_BYTES // 1024, len(b) - MAX_TOOL_BYTES + 80)
    return {"content": [{"type": "text", "text": t}], "isError": error}


def _lib_view():
    out = {}
    for k, v in LIBRARY.items():
        opts = {n: {kk: vv for kk, vv in s.items() if kk != "kind"} | {"type": s["kind"]} for n, s in {**cfgmod.COMMON_OPTIONS, **v["options"]}.items()}
        out[k] = {"label": v["label"], "description": v["description"], "default_title": v["default_title"], "options": opts,
                  "raw_shape": v["raw_shape"], "real_source": v["real_source"],
                  "csv": "a .csv file also works: header row = the field names of each item in raw_shape.%s, one row per item (top-level fields use defaults)" % v["csv_list_key"]}
    return out


def call_tool(name, a):
    home = cfgmod.home_dir()
    if name == "setup":
        no = a.get("no_open", False)
        if not isinstance(no, bool):
            raise ConfigError("no_open must be a boolean")
        cfg, created, added = cfgmod.setup(a.get("title"), a.get("panels"), home)
        res = open_dashboard(home, no_open=no)
        out = {"created": created, "added": added, "dashboard_folder": home, "data_folder": os.path.join(home, "data"),
               "keys_file": cfgmod.env_file_path(home), "panels": [{"id": p["id"], "type": p["type"], "source_mode": p["source"]["mode"]} for p in cfg["panels"]],
               "page": res["path"], "opened": res["opened"], "build": res["panels"],
               "sample_data": "all panels show fictional SAMPLE data until a real source is connected",
               "suggest": ["add my X stats", "move sales to the top", "open my dashboard", "remove the ads panel"]}
        if "hint" in res:
            out["hint"] = res["hint"]
        return out
    if name == "init":
        cfg = cfgmod.init(a.get("title", "My dashboard"), a.get("panels"), bool(a.get("force")), home)
        return {"panels": [p["id"] for p in cfg["panels"]], "next": "call rebuild, then open out/dashboard.html", "dashboard_folder": home}
    if name == "list_panel_library":
        v = _lib_view()
        t = a.get("type")
        if t is not None:
            if t not in v:
                raise ConfigError(cfgmod.fix("unknown panel type %r (available: %s)" % (t, ", ".join(v)), cfgmod.HINT_TYPES))
            return {t: v[t]}
        return v
    if name == "list_panels":
        cfg = cfgmod.load(home)
        return {"title": cfg["title"], "panels": [{"id": p["id"], "type": p["type"], "title": p["title"], "source_mode": p["source"]["mode"],
                                                   "options": p["options"]} for p in cfg["panels"]]}
    if name == "add_panel":
        if "type" not in a:
            raise ConfigError("type is required")
        return cfgmod.add_panel(a["type"], a.get("id"), a.get("title"), a.get("source"), a.get("options"), a.get("position"), home)
    if name == "update_panel":
        if "id" not in a:
            raise ConfigError("id is required")
        return cfgmod.update_panel(a["id"], a.get("title"), a.get("source"), a.get("options"), home)
    if name == "remove_panel":
        if "id" not in a:
            raise ConfigError("id is required")
        return cfgmod.remove_panel(a["id"], home)
    if name == "reorder_panels":
        if "order" not in a:
            raise ConfigError("order is required")
        return cfgmod.reorder_panels(a["order"], home)
    if name == "open_dashboard":
        no = a.get("no_open", False)
        if not isinstance(no, bool):
            raise ConfigError("no_open must be a boolean")
        return open_dashboard(home, no_open=no)
    if name == "rebuild":
        return build(home)
    raise KeyError(name)


def handle(msg):
    """Return a response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    is_note = "id" not in msg

    def err(code, text):
        return None if is_note else {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    def ok(result):
        return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": result}
    if not isinstance(method, str):
        return err(-32600, "invalid request")
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in SUPPORTED else SUPPORTED[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "dashboard", "version": __version__}})
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, "unknown tool %r" % name)
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        schema = next(t for t in TOOLS if t["name"] == name)["inputSchema"]
        extra = set(args) - set(schema["properties"])
        if extra:
            return ok(_text("unknown argument(s): %s" % ", ".join(sorted(extra)), True))
        try:
            return ok(_text(call_tool(name, args)))
        except (ConfigError, ValueError) as e:
            return ok(_text("error: %s" % e, True))
        except LeakError as e:
            return ok(_text("SECRET LEAK GUARD: %s" % e, True))
        except OSError as e:
            return ok(_text("error: %s" % cfgmod.fix("a file or folder problem (%s)" % (e.strerror or type(e).__name__),
                                                     "check the dashboard folder exists and you may write to it, or set DASHBOARD_HOME to a folder you own"), True))
        except Exception as e:  # never crash the server, never echo internals
            return ok(_text("internal error (%s)" % type(e).__name__, True))
    return err(-32601, "method not found: %s" % method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
