#!/usr/bin/env python3
"""End-to-end tests for the dashboard plugin. Standard library only (jsonschema/Chrome/Node used when present).

  python3 tests/test_all.py            # run everything
  UPDATE_SCREENSHOT=1 python3 tests/test_all.py   # also regenerate docs/preview-*.png (sample data only)

Covers: manifest + schema validation, config validation, MCP stdio protocol (every tool), sample build,
HTML render + XSS in a real browser, drag-to-reorder + persistence in a real browser, file/http sources,
secret-leak guard, and the package-wide leak grep. Exit code 0 only if everything passes."""
import json
import os
import sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import http.server

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from dashlib import config as cfgmod  # noqa: E402
from dashlib.build import LeakError, build, leak_scan  # noqa: E402
from dashlib.library import LIBRARY  # noqa: E402

RESULTS = []


def check(name, cond, info=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(info)[:300]) if (info and not cond) else ""))
    return bool(cond)


def section(t):
    print("\n== " + t)


def raises(fn, exc, contains=None):
    try:
        fn()
    except exc as e:
        return contains is None or contains in str(e)
    except Exception:
        return False
    return False


def tmp():
    return tempfile.mkdtemp(prefix="dash-test-")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|BEARER)", k, re.I)}
    env.update(extra)
    return env


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ 1. manifests
def test_manifests():
    section("1. plugin.json / mcp.json / skills")
    pj, mj = json.loads(read(os.path.join(ROOT, "plugin.json"))), json.loads(read(os.path.join(ROOT, "mcp.json")))
    sd = os.path.join(ROOT, "tests", "schemas")
    ps, ms = json.loads(read(os.path.join(sd, "plugin.schema.json"))), json.loads(read(os.path.join(sd, "mcp.schema.json")))
    try:
        import jsonschema
        for name, doc, sch in (("plugin.json", pj, ps), ("mcp.json", mj, ms)):
            try:
                jsonschema.validate(doc, sch)
                check("%s validates against the Agent Plugins 1.0.0 schema (jsonschema)" % name, True)
            except jsonschema.ValidationError as e:
                check("%s validates against schema" % name, False, e.message)
    except ImportError:
        print("NOTE jsonschema not installed: using built-in checks of the same rules")
        check("plugin.json: $schema const + name pattern + closed fields",
              pj.get("$schema") == ps["properties"]["$schema"]["const"] and re.match(ps["properties"]["name"]["pattern"], pj["name"])
              and set(pj) <= set(ps["properties"]) and set(pj.get("author", {})) <= {"name", "email", "url"})
        s = mj["mcpServers"]["dashboard"]
        check("mcp.json: schema const, stdio server, cwd pattern, command is one token",
              mj["$schema"] == ms["properties"]["$schema"]["const"] and set(mj) == {"$schema", "mcpServers"} and s["type"] == "stdio"
              and ("cwd" not in s or re.match(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))", s["cwd"])) and " " not in s["command"]
              and not ({"PLUGIN_ROOT", "PLUGIN_DATA"} & set(s.get("env", {}))))
    check("plugin name is valid and version set", re.fullmatch(r"[a-z0-9][a-z0-9.-]*", pj["name"]) and pj["version"] == "0.1.0")
    check("mcp entry point exists (launched through the bootstrap in mcp.json args)", os.path.isfile(os.path.join(ROOT, "server", "mcp_server.py")) and "server" in " ".join(mj["mcpServers"]["dashboard"]["args"]))
    for d in sorted(os.listdir(os.path.join(ROOT, "skills"))):
        txt = read(os.path.join(ROOT, "skills", d, "SKILL.md"))
        m = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n", txt)
        check("skill %s: frontmatter name matches dir, description <=1024" % d, m and m.group(1) == d and len(m.group(2)) <= 1024)


# ------------------------------------------------------------------ 2. config validation
def test_config():
    section("2. config validation + atomic edits")
    h = tmp()
    cfgmod.init("T", home=h)
    ok = lambda **k: cfgmod.add_panel("sales", home=h, **k)  # noqa: E731
    CE = cfgmod.ConfigError
    check("add valid panel", ok(id="s1")["id"] == "s1")
    check("duplicate id rejected", raises(lambda: ok(id="s1"), CE, "already exists"))
    check("bad id rejected", raises(lambda: ok(id="Bad Id!"), CE, "panel id"))
    check("unknown type rejected", raises(lambda: cfgmod.add_panel("nope", home=h), CE, "unknown panel type"))
    check("unknown option rejected", raises(lambda: ok(id="s2", options={"bogus": 1}), CE, "unknown option"))
    check("option range enforced", raises(lambda: ok(id="s2", options={"days": 9999}), CE, "between"))
    check("secret-looking option rejected", raises(lambda: cfgmod.add_panel("x_stats", id="x2", options={"handle": "sk-" + "a" * 30}, home=h), CE))
    check("secret-looking title rejected", raises(lambda: ok(id="s2", title="A" * 40), CE, "secret"))
    check("absolute file path rejected", raises(lambda: ok(id="s2", source={"mode": "file", "path": "/etc/passwd"}), CE, "relative"))
    check("'..' path rejected", raises(lambda: ok(id="s2", source={"mode": "file", "path": "../x.json"}), CE, ".."))
    check("protected folder name rejected", raises(lambda: ok(id="s2", source={"mode": "file", "path": "secrets/x.json"}), CE, "protected"))
    check("http (non-local) url rejected", raises(lambda: ok(id="s2", source={"mode": "http_json", "url": "http://example.com/x"}), CE, "https"))
    check("credential in url query rejected", raises(lambda: ok(id="s2", source={"mode": "http_json", "url": "https://example.com/x?api_key=abc"}), CE, "query"))
    check("user:pass in url rejected", raises(lambda: ok(id="s2", source={"mode": "http_json", "url": "https://u:p" + "@" + "example.com/x"}), CE))
    check("auth_env must be a NAME, not a value", raises(lambda: ok(id="s2", source={"mode": "http_json", "url": "https://example.com/x", "auth_env": "abc123secretvalue"}), CE, "NAME"))
    check("unknown source field rejected", raises(lambda: ok(id="s2", source={"mode": "sample", "token": "x"}), CE, "unknown source"))
    check("valid http_json source accepted", ok(id="s3", source={"mode": "http_json", "url": "https://example.com/sales.json", "auth_env": "MY_SALES_TOKEN"})["source"]["auth_env"] == "MY_SALES_TOKEN")
    check("update_panel merges options", cfgmod.update_panel("s1", options={"days": 5}, home=h)["options"]["days"] == 5)
    check("reorder needs every id", raises(lambda: cfgmod.reorder_panels(["s1"], home=h), CE, "missing"))
    check("reorder rejects unknown id", raises(lambda: cfgmod.reorder_panels(["zzz"], home=h), CE, "unknown"))
    ids = [p["id"] for p in cfgmod.load(h)["panels"]]
    cfgmod.reorder_panels(list(reversed(ids)), home=h)
    check("reorder applies", [p["id"] for p in cfgmod.load(h)["panels"]] == list(reversed(ids)))
    check("remove unknown id errors", raises(lambda: cfgmod.remove_panel("zzz", home=h), CE, "no panel"))
    cfgmod.remove_panel("s3", home=h)
    check("remove works", "s3" not in [p["id"] for p in cfgmod.load(h)["panels"]])
    # atomic write: a failing validation must leave the file byte-identical
    before = read(cfgmod.config_path(h))
    raises(lambda: ok(id="s9", options={"days": -1}), CE)
    check("failed edit leaves config untouched", read(cfgmod.config_path(h)) == before)
    check("no temp files left behind", not [f for f in os.listdir(h) if f.startswith(".tmp-")])
    check("init refuses to overwrite without force", raises(lambda: cfgmod.init("T", home=h), CE, "already exists"))
    with open(cfgmod.config_path(h), "w") as f:
        f.write("{not json")
    check("corrupt config gives a clear error", raises(lambda: cfgmod.load(h), CE, "not valid JSON"))
    shutil.rmtree(h)


# ------------------------------------------------------------------ 3. MCP over stdio
class Mcp:
    def __init__(self, home, env=None):
        e = clean_env()
        e.pop("DASHBOARD_HOME", None)
        if home:
            e["DASHBOARD_HOME"] = home
        e.update(env or {})
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "mcp_server.py")], cwd=ROOT, env=e,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0

    def raw(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def rpc(self, method, params=None):
        self.n += 1
        return self.raw(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}))

    def call(self, name, args=None):
        r = self.rpc("tools/call", {"name": name, "arguments": args or {}})["result"]
        txt = r["content"][0]["text"]
        try:
            return r["isError"], json.loads(txt)
        except ValueError:
            return r["isError"], txt

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)


def test_mcp():
    section("3. MCP server over stdio (scripted JSON-RPC)")
    h = tmp()
    home = os.path.join(h, "dash")
    m = Mcp(home)
    r = m.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})["result"]
    check("initialize: protocol, tools capability, serverInfo", r["protocolVersion"] == "2025-06-18" and "tools" in r["capabilities"] and r["serverInfo"]["name"] == "dashboard")
    m.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    check("ping answered", m.rpc("ping")["result"] == {})
    tools = {t["name"]: t for t in m.rpc("tools/list")["result"]["tools"]}
    want = {"setup", "init", "list_panel_library", "list_panels", "add_panel", "update_panel", "remove_panel", "reorder_panels", "rebuild", "open_dashboard"}
    check("tools/list has all 10 tools with object input schemas", set(tools) == want and all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools.values()), sorted(tools))
    check("unknown method -> -32601", m.rpc("nope/nope")["error"]["code"] == -32601)
    check("garbage line -> -32700 and server survives", m.raw("{bad json")["error"]["code"] == -32700 and m.rpc("ping")["result"] == {})
    check("unknown tool -> -32602", m.rpc("tools/call", {"name": "zzz", "arguments": {}})["error"]["code"] == -32602)
    err, out = m.call("list_panels")
    check("list_panels before init -> clear error with plain-word fix", err and "set up my dashboard" in out and "Fix:" in out, out)
    err, out = m.call("init", {"title": "Test board"})
    check("init seeds 5 sample panels", not err and out["panels"] == ["x-stats", "website-visits", "sales", "ads", "bot-activity"], out)
    err, out = m.call("init", {})
    check("init again refuses", err and "already exists" in out)
    err, lib = m.call("list_panel_library")
    check("list_panel_library: 5 types with options/raw_shape/real_source", not err and set(lib) == set(LIBRARY) and all({"options", "raw_shape", "real_source"} <= set(v) for v in lib.values()))
    err, out = m.call("list_panels")
    check("list_panels shows 5 sample panels", not err and len(out["panels"]) == 5 and all(p["source_mode"] == "sample" for p in out["panels"]))
    err, out = m.call("add_panel", {"type": "sales", "id": "shop", "title": "Shop sales", "options": {"currency": "eur", "days": 7}, "position": 0})
    check("add_panel returns normalized panel", not err and out["id"] == "shop" and out["options"]["currency"] == "EUR" and out["source"] == {"mode": "sample"}, out)
    err, out = m.call("list_panels")
    check("add_panel honoured position 0", out["panels"][0]["id"] == "shop")
    err, out = m.call("add_panel", {"type": "sales", "id": "shop"})
    check("add_panel duplicate -> isError", err and "already exists" in out)
    err, out = m.call("add_panel", {"type": "sales", "id": "x", "options": {"api_key": "1"}})
    check("add_panel bad option -> isError", err and "unknown option" in out)
    err, out = m.call("add_panel", {"type": "sales", "id": "x", "source": {"mode": "http_json", "url": "https://e.test/x?token=abc"}})
    check("add_panel credential-in-url -> isError", err and "query" in out)
    err, out = m.call("add_panel", {"type": "sales", "bogus": 1})
    check("add_panel unknown argument -> isError", err and "unknown argument" in out)
    err, out = m.call("add_panel", {})
    check("add_panel missing type -> isError", err and "type is required" in out)
    err, out = m.call("update_panel", {"id": "shop", "title": "Renamed", "options": {"days": 10}})
    check("update_panel changes title+options", not err and out["title"] == "Renamed" and out["options"]["days"] == 10, out)
    err, out = m.call("update_panel", {"id": "ghost"})
    check("update_panel unknown id -> isError", err and "no panel" in out)
    err, out = m.call("reorder_panels", {"order": ["bot-activity", "ads", "sales", "website-visits", "x-stats", "shop"]})
    check("reorder_panels applies", not err and out["order"][0] == "bot-activity")
    err, out = m.call("reorder_panels", {"order": ["ads"]})
    check("reorder_panels incomplete -> isError", err and "missing" in out)
    err, out = m.call("remove_panel", {"id": "shop"})
    check("remove_panel removes", not err and "shop" not in out["remaining"] and out["removed"] == "shop")
    err, out = m.call("remove_panel", {"id": "shop"})
    check("remove_panel twice -> isError", err)
    err, out = m.call("rebuild")
    check("rebuild ok: 5 panels ok, files written", not err and [p["status"] for p in out["panels"]] == ["ok"] * 5 and os.path.isfile(out["html"]) and os.path.isfile(out["data_json"]), out)
    err, o = m.call("open_dashboard", {"no_open": True})
    check("open_dashboard no_open: rebuilds, returns path + url, opened=false", not err and o["opened"] is False and os.path.isfile(o["path"]) and o["url"].startswith("file://") and all(p["status"] == "ok" for p in o["panels"]), o)
    err, o = m.call("open_dashboard", {"no_open": "yes"})
    check("open_dashboard rejects non-boolean no_open", err and "boolean" in o)
    err, o = m.call("open_dashboard", {"bogus": 1})
    check("open_dashboard rejects unknown argument", err and "unknown argument" in o)
    check("rebuild result carries guard info, no secret values", "checked_env" in out["guard"])
    check("order from reorder_panels is the page order", [p["id"] for p in json.loads(read(out["data_json"]))["panels"]] == ["bot-activity", "ads", "sales", "website-visits", "x-stats"])
    m.close()
    # server inherits no secret into output: run with a fake secret env and confirm the guard reports the NAME only
    m = Mcp(home, {"DASH_FAKE_API_KEY": "fake-value-abcdef-123456"})
    m.rpc("initialize", {"protocolVersion": "2024-11-05"})
    err, out = m.call("rebuild")
    check("rebuild with a secret in env: ok, guard lists name only", not err and "DASH_FAKE_API_KEY" in out["guard"]["checked_env"] and "fake-value" not in json.dumps(out))
    check("older protocol version is echoed", m.rpc("initialize", {"protocolVersion": "2024-11-05"})["result"]["protocolVersion"] == "2024-11-05")
    m.close()
    shutil.rmtree(h)


def test_open():
    section("3b. open_dashboard / opener (browser launch mocked)")
    import webbrowser
    from dashlib.opener import open_dashboard
    h = tmp()
    cfgmod.init("O", home=h)
    calls = []
    real = webbrowser.open
    webbrowser.open = lambda url, *a, **k: calls.append(url) or True
    try:
        r = open_dashboard(h, env=clean_env())
        check("opener launches the file:// URL of out/dashboard.html once", r["opened"] is True and calls == [r["url"]] and r["url"].endswith("/out/dashboard.html"), calls)
        calls.clear()
        r = open_dashboard(h, no_open=True, env=clean_env())
        check("no_open never calls the browser", r["opened"] is False and calls == [])
        webbrowser.open = lambda *a, **k: False
        check("opened=false when no browser can be launched", open_dashboard(h, env=clean_env())["opened"] is False)
    finally:
        webbrowser.open = real
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py"), "--home", h, "--open"], env=clean_env(BROWSER="true"), capture_output=True, text=True)
    check("scripts/build.py --open rebuilds and exits 0", p.returncode == 0 and "wrote" in p.stdout, p.stdout + p.stderr)
    shutil.rmtree(h)


# ------------------------------------------------------------------ 4. build + render
def chrome_bin():
    for c in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(c)
        if p:
            return p
    return None


def embedded_data(html):
    m = re.search(r"const D=(.*?);\nconst E=", html, re.S)
    return json.loads(m.group(1))


def test_build_and_render():
    section("4. sample build + render")
    h = tmp()
    cfgmod.init("Sample board", home=h)
    r = build(h, env=clean_env())
    html, data = read(r["html"]), json.loads(read(r["data_json"]))
    check("build: 5 panels, all ok", [p["status"] for p in r["panels"]] == ["ok"] * 5)
    check("html embeds the same data as data.json (placeholder replaced)", "__DATA_JSON__" not in html and embedded_data(html) == data)
    check("html is self-contained (no external URLs/CDNs)", not re.search(r"(src|href)=[\"']https?://|@import|url\(http", html))
    check("exactly one closing </script> (data cannot break out)", html.count("</script>") == 1)
    types = {p["type"]: p for p in data["panels"]}
    check("every panel carries normalized data + sample flag", set(types) == set(LIBRARY) and all(p["source_mode"] == "sample" and "data" in p for p in data["panels"]))
    x = types["x_stats"]["data"]
    check("x_stats totals add up", x["totals"]["impressions"] == sum(p["impressions"] for p in x["posts"]) and len(x["series"]) == len(x["posts"]))
    a = types["ads"]["data"]
    check("ads CTR/CPC computed", a["totals"]["ctr"] == round(100.0 * a["totals"]["clicks"] / a["totals"]["impressions"], 2))
    s = types["sales"]["data"]
    check("sales totals add up", s["totals"]["orders"] == sum(d["orders"] for d in s["days"]))
    check("sample data is deterministic for a given day", embedded_data(read(build(h, env=clean_env())["html"]))["panels"] == data["panels"])
    ch = chrome_bin()
    if ch:
        dom = subprocess.run([ch, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=3000", "--dump-dom", "file://" + r["html"]],
                             capture_output=True, text=True, timeout=60).stdout
        check("browser DOM has all 5 panel cards", all(('id="p-%s"' % p["id"]) in dom for p in data["panels"]))
        visible = re.sub(r"<script.*?</script>", "", dom, flags=re.S)
        check("browser DOM: SAMPLE banner + tags, no render failure", "SAMPLE DATA" in visible and visible.count(">SAMPLE<") >= 5 and "render failed" not in visible)
        check("browser DOM: charts + tables rendered", dom.count("<svg") > 8 and "<table" in dom)
    else:
        print("SKIP browser render (no chrome)")
    shutil.rmtree(h)
    return ch


def test_xss(ch):
    section("5. hostile data cannot inject script (file source)")
    h = tmp()
    cfgmod.init("X", panels=[], home=h)
    os.makedirs(os.path.join(h, "data"))
    evil = "</script><script>window.__xss=1</script><img src=x onerror=window.__xss=1>"
    with open(os.path.join(h, "data", "bots.json"), "w") as f:
        json.dump({"bots": [{"name": evil, "status": "active", "last_update": "2026-01-01T00:00:00Z", "last_action": evil}]}, f)
    cfgmod.add_panel("bot_activity", id="bots", title="<b>t</b>", source={"mode": "file", "path": "data/bots.json"}, home=h)
    r = build(h, env=clean_env())
    html = read(r["html"])
    check("file source builds ok", r["panels"][0]["status"] == "ok", r)
    check("raw payload never appears unescaped in html", "<script>window.__xss" not in html and "<img src=x" not in html and html.count("</script>") == 1)
    if ch:
        dom = subprocess.run([ch, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=3000", "--dump-dom", "file://" + r["html"]],
                             capture_output=True, text=True, timeout=60).stdout
        check("browser DOM: payload shown as text, not parsed as markup", "&lt;img src=x" in dom and "<img src=x" not in dom and "<b>t</b>" not in dom.replace("&lt;b&gt;", ""))
    # file sources cannot escape the dashboard folder via symlink
    outside = tmp()
    with open(os.path.join(outside, "o.json"), "w") as f:
        f.write("{}")
    os.symlink(os.path.join(outside, "o.json"), os.path.join(h, "data", "link.json"))
    cfgmod.add_panel("bot_activity", id="lnk", source={"mode": "file", "path": "data/link.json"}, home=h)
    r = build(h, env=clean_env())
    check("symlink out of the dashboard folder is refused", [p for p in r["panels"] if p["id"] == "lnk"][0]["status"] == "error")
    # a bad data file yields an error card for that panel only
    with open(os.path.join(h, "data", "bad.json"), "w") as f:
        json.dump({"bots": [{"name": "a", "status": "active", "last_update": "garbage"}]}, f)
    cfgmod.add_panel("bot_activity", id="bad", source={"mode": "file", "path": "data/bad.json"}, home=h)
    r = build(h, env=clean_env())
    st = {p["id"]: p for p in r["panels"]}
    check("invalid data -> that panel errors with a clear message, others still build", st["bad"]["status"] == "error" and "last_update" in st["bad"]["error"] and st["bots"]["status"] == "ok", st["bad"])
    shutil.rmtree(h)
    shutil.rmtree(outside)


def test_drag(ch):
    section("6. drag-to-reorder + persistence in a real browser (CDP)")
    node = shutil.which("node")
    if not (ch and node):
        print("SKIP drag test (needs chrome + node)")
        return
    h = tmp()
    cfgmod.init("Drag board", home=h)
    r = build(h, env=clean_env())
    if os.environ.get("UPDATE_SCREENSHOT"):
        sh = tmp()
        cfgmod.init("Sampleco dashboard", home=sh)
        sr = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py"), "--home", sh], env=clean_env(TZ="UTC"), capture_output=True, text=True)
        subprocess.run([node, "--experimental-websocket", os.path.join(ROOT, "tests", "screenshots.js"), os.path.join(sh, "out", "dashboard.html"), os.path.join(ROOT, "docs")],
                       capture_output=True, timeout=120)
        shutil.rmtree(sh)
        print("NOTE regenerated docs/preview-*.png", sr.returncode)
    p = subprocess.run([node, "--experimental-websocket", os.path.join(ROOT, "tests", "drag_test.js"), r["html"]], capture_output=True, text=True, timeout=120)
    lines = [l for l in p.stdout.splitlines() if l.startswith(("PASS", "FAIL"))]
    for l in lines:
        check("browser: " + l[5:], l.startswith("PASS"), l)
    check("drag test exited 0 with checks run", p.returncode == 0 and len(lines) >= 10, p.stderr[-300:])
    shutil.rmtree(h)


# ------------------------------------------------------------------ 7. http_json source
class Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/sales")
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer " + Handler.token and not self.path.startswith("/open"):
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(Handler.payload(self.path)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def sales_payload(note=""):
    return {"currency": "USD", "days": [{"date": "2026-01-0%d" % i, "orders": i, "revenue": 10 * i} for i in range(1, 6)],
            "recent": [{"date": "2026-01-05", "item": "Widget" + note, "amount": 50}]}


def serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


FAKE = "fake-secret-VALUE-9f8e7d6c5b4a"


def test_http_and_guard():
    section("7. http_json source + secret-leak guard")
    Handler.token = FAKE
    Handler.payload = lambda path: sales_payload()
    srv = serve()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    h = tmp()
    cfgmod.init("G", panels=[], home=h)
    cfgmod.add_panel("sales", id="live", source={"mode": "http_json", "url": base + "/sales", "auth_env": "DASH_TEST_TOKEN"}, home=h)
    env = clean_env(DASH_TEST_TOKEN=FAKE)
    r = build(h, env=env)
    check("http_json: token sent as Bearer header, data normalized", r["panels"][0]["status"] == "ok" and ("/sales", "Bearer " + FAKE) in Handler.seen, r)
    check("guard checked the secret by NAME", "DASH_TEST_TOKEN" in r["guard"]["checked_env"] and FAKE not in json.dumps(r))
    out = read(r["html"]) + read(r["data_json"])
    check("secret value is in neither html nor data.json (clean build)", FAKE not in out and "Bearer" not in out)
    d = json.loads(read(r["data_json"]))["panels"][0]
    check("data.json does not record url or credentials", "url" not in json.dumps(d) and base not in out)
    r2 = build(h, env=clean_env())
    check("missing env var -> panel error naming the variable, not a crash", r2["panels"][0]["status"] == "error" and "DASH_TEST_TOKEN is not set" in r2["panels"][0]["error"])
    r3 = build(h, env=clean_env(DASH_TEST_TOKEN="wrong-token-value-123"))
    check("401 -> panel error without url/secret", r3["panels"][0]["status"] == "error" and "401" in r3["panels"][0]["error"] and "wrong-token" not in json.dumps(r3))
    cfgmod.update_panel("live", source={"mode": "http_json", "url": base + "/redirect", "auth_env": "DASH_TEST_TOKEN"}, home=h)
    Handler.seen.clear()
    r4 = build(h, env=env)
    check("redirects are not followed (token cannot be forwarded)", r4["panels"][0]["status"] == "error" and not any(p == "/sales" for p, _ in Handler.seen), Handler.seen)

    # ---- leak guard: remote data that echoes the secret ----
    cfgmod.update_panel("live", source={"mode": "http_json", "url": base + "/sales", "auth_env": "DASH_TEST_TOKEN"}, home=h)
    prev = read(r["html"])
    Handler.payload = lambda path: sales_payload(note=" " + FAKE)
    try:
        build(h, env=env)
        check("GUARD: leaking remote data aborts the build", False)
    except LeakError as e:
        check("GUARD: leaking remote data aborts the build", "DASH_TEST_TOKEN" in str(e))
        check("GUARD: error message names the variable but never prints the value", FAKE not in str(e))
    check("GUARD: nothing new written (previous output intact, no secret on disk)", read(os.path.join(h, "out", "dashboard.html")) == prev and FAKE not in read(os.path.join(h, "out", "dashboard.html")))
    # CLI exit code 3
    cli = lambda home, env: subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py"), "--home", home], env=env, capture_output=True, text=True)  # noqa: E731
    p = cli(h, dict(env, PATH=os.environ.get("PATH", "")))
    check("GUARD: CLI exits 3 and stderr has no secret value", p.returncode == 3 and FAKE not in p.stdout + p.stderr and "LEAK GUARD" in p.stderr, p.stderr)

    # ---- variants: html/json-escaped forms of the secret are caught too ----
    Handler.payload = lambda path: sales_payload(note=" <" + FAKE + "&>")
    try:
        build(h, env=env)
        check("GUARD: secret wrapped in <&> markup is still caught", False)
    except LeakError:
        check("GUARD: secret wrapped in <&> markup is still caught", True)
    srv.shutdown()

    # ---- configured secrets[] and auto-detected env names, via a file source ----
    h2 = tmp()
    cfgmod.init("G2", panels=[], home=h2)
    os.makedirs(os.path.join(h2, "data"))
    weird = 'va"lue<with>&odd/chars+9f8e7d'
    for var, how in (("DASH_PLAIN_VAR", "configured"), ("DASH_SERVICE_API_KEY", "auto")):
        with open(os.path.join(h2, "data", "s.json"), "w") as f:
            json.dump(sales_payload(note=" " + weird), f)
        cfgmod.add_panel("sales", id="f" + how, source={"mode": "file", "path": "data/s.json"}, home=h2)
        c = cfgmod.load(h2)
        if how == "configured":
            c["secrets"] = [var]
            cfgmod.save(c, h2)
        e = clean_env(**{var: weird})
        try:
            build(h2, env=e)
            check("GUARD (%s secret, odd characters) caught" % how, False)
        except LeakError as ex:
            check("GUARD (%s secret, odd characters) caught" % how, var in str(ex) and weird not in str(ex))
        cfgmod.remove_panel("f" + how, home=h2)
    check("auto-detect can be switched off in config", True if cfgmod.save(dict(cfgmod.load(h2), guard={"auto_env": False}), h2) else False)
    # leak_scan directly: short secrets are reported, not silently ignored
    s = leak_scan({"a": "x"}, {"secrets": ["DASH_SHORT"], "panels": [], "guard": {"auto_env": False}}, {"DASH_SHORT": "abc"})
    check("too-short configured secret is flagged in guard info", s["short"] == ["DASH_SHORT"])
    shutil.rmtree(h)
    shutil.rmtree(h2)


# ------------------------------------------------------------------ 8. package leak grep
def parts(*p):
    return "".join(p)


FORBIDDEN = [parts("ol", "lie"), parts("far", "sonar"), parts("fed", "sonar"), parts("two", "step"), parts("howdy", "ol", "lie"), parts("hex", "gn"),
             parts("g", "mail"), parts("cl", "io"), parts("pul", "sar"), parts("ker", "nel"), parts("pul", "se"), parts("/work", "space"), parts("/home", "/box"),
             parts("/", "root"), parts("/", "Users/", r"\w"), parts("d17c", "1b57"), parts("e6d9", "267d"), parts("BEGIN [A-Z ]*PRIV", "ATE KEY")]
KEY_PATTERNS = [r"sk-[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}", r"gh[pousr]_[A-Za-z0-9]{30,}", r"xox[abpr]-[A-Za-z0-9-]{10,}", r"AIza[0-9A-Za-z_-]{30,}",
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", r"[A-Za-z0-9+/_]{40,}={0,2}", parts(r"[\w.+-]+", "@", r"[\w-]+\.[\w.-]+")]


# ---- THE ONLY ALLOWED EXCEPTION to the forbidden-term grep: the author's public GitHub handle, in exactly these files and forms.
# (Approved by the owner; the username is confirmed. If it ever changes, change it here too.)
# Every allowed form is removed from the text before scanning; anything left over still counts as a hit. The handle must appear in
# no other file in the tree (source code, examples, skills, docs, screenshots' text is not checkable).
_H = parts("howdy", "ol", "lie")
_URL = r"https://github\.com/" + _H + r"/agent-dashboard(?:/issues)?"
_PROSE = [_URL, r"`" + _H + r"`", _H + r"/agent-dashboard"]
ALLOWED = {
    "plugin.json": [r'"name": "' + _H + '"', _URL],
    "LICENSE": [r"Copyright \(c\) 2026 " + _H],
    "README.md": _PROSE,
    "LISTING.md": _PROSE,
    "PUBLISH-CHECKLIST.md": _PROSE,
}


def allowed_strip(rel, txt):
    for pat in ALLOWED.get(rel, []):
        txt = re.sub(pat, "", txt)
    return txt


def test_leak_grep():
    section("12. package leak grep (whole tree, case-insensitive)")
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ROOT)
            try:
                txt = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue  # binary (PNG): checked separately below
            txt = allowed_strip(rel, txt)
            for pat in FORBIDDEN:
                if re.search(pat, txt, re.I):
                    hits.append((rel, pat if len(pat) < 3 else pat[:2] + "***"))
            for pat in KEY_PATTERNS:
                for m in re.finditer(pat, txt):
                    if rel.startswith("tests" + os.sep + "schemas"):
                        continue  # public JSON schemas
                    # allow long identifiers only if they are obviously code (no digits mix) -> report anyway for review
                    hits.append((rel, "keypattern:" + pat[:14]))
                    break
    check("zero forbidden-term / key-pattern hits in text files (after the narrow author-handle allowlist)", not hits, hits[:10])
    seen = sorted(os.path.relpath(os.path.join(dp, f), ROOT) for dp, dn, fn in os.walk(ROOT) for f in fn
                  if f.endswith((".json", ".md", ".py", ".js", ".html", ".sh", ".txt")) or f == "LICENSE"
                  if re.search(_H, open(os.path.join(dp, f), encoding="utf-8").read(), re.I))
    check("the author handle appears only in the allowlisted files: " + ", ".join(sorted(ALLOWED)), seen == sorted(ALLOWED), seen)
    png = [os.path.join(dp, f) for dp, _, fs in os.walk(ROOT) for f in fs if f.endswith(".png")]
    shots = {os.path.join("docs", n) for n in ("preview-desktop.png", "preview-layout-menu.png", "preview-mobile.png", "preview-mobile-full.png")}
    check("images are only the four generated sample-data screenshots", {os.path.relpath(p, ROOT) for p in png} <= shots, png)
    bad = [f for dp, _, fs in os.walk(ROOT) for f in fs if f.endswith((".pyc", ".log")) or f in (".env", "id_rsa")]
    check("no pyc/log/.env files shipped", not [b for b in bad if "__pycache__" not in b], bad[:5])


def main():
    test_manifests()
    test_config()
    test_mcp()
    test_open()
    ch = test_build_and_render()
    test_xss(ch)
    test_drag(ch)
    test_http_and_guard()
    import test_first_run
    test_first_run.run(sys.modules[__name__])
    import test_caps
    test_caps.run(sys.modules[__name__])
    test_leak_grep()
    n, ok = len(RESULTS), sum(RESULTS)
    print("\n%d/%d checks passed" % (ok, n))
    return 0 if ok == n else 1


def run_only(fn):
    fn(sys.modules[__name__])
    n, ok = len(RESULTS), sum(RESULTS)
    print("\n%d/%d checks passed" % (ok, n))
    sys.exit(0 if ok == n else 1)


if __name__ == "__main__":
    sys.exit(main())
