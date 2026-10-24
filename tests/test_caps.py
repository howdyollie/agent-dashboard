"""Usage-safety tests: caps, timeouts, no retries, small tool answers, an idle server, a static page.
Run as part of tests/test_all.py. `python3 tests/test_caps.py` runs only this file."""
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True


class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class H(http.server.BaseHTTPRequestHandler):
    hits = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        H.hits[self.path] = H.hits.get(self.path, 0) + 1
        try:
            if self.path == "/big":
                body = b'{"days": [], "pad": "' + b"x" * (3 * 1024 * 1024) + b'"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/slow":
                time.sleep(6)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            elif self.path == "/drip":
                self.send_response(200)
                self.end_headers()
                for _ in range(60):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.25)
                self.wfile.write(b"{}")
            elif self.path == "/500":
                self.send_response(500)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass


def run(T):
    check, section, Mcp, tmp, read = T.check, T.section, T.Mcp, T.tmp, T.read
    ROOT, cfgmod, build = T.ROOT, T.cfgmod, T.build
    CE = cfgmod.ConfigError
    section("13. usage caps: size, time, retries, panels, rows, tool answer size")
    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    # ---- max panels
    h = tmp()
    cfgmod.init("Caps", panels=[], home=h)
    for i in range(cfgmod.MAX_PANELS):
        cfgmod.add_panel("bot_activity", id="b%d" % i, home=h)
    check("%d panels is allowed" % cfgmod.MAX_PANELS, len(cfgmod.load(h)["panels"]) == cfgmod.MAX_PANELS)
    check("one more panel is refused with a plain-word fix", T.raises(lambda: cfgmod.add_panel("bot_activity", id="one-too-many", home=h), CE, "at most 20 panels"))
    check("config file with too many panels is refused on load", T.raises(lambda: cfgmod.validate_config({"panels": [{"id": "p%d" % i, "type": "sales"} for i in range(21)]}), CE, "at most"))
    shutil.rmtree(h)

    # ---- file source caps
    h = tmp()
    cfgmod.init("Caps", panels=[], home=h)
    os.makedirs(os.path.join(h, "data"))
    with open(os.path.join(h, "data", "huge.json"), "w") as f:
        f.write('{"days": [], "pad": "' + "x" * (2 * 1024 * 1024 + 10) + '"}')
    with open(os.path.join(h, "data", "deep.json"), "w") as f:
        f.write("[" * 1_000_000)
    with open(os.path.join(h, "data", "rows.json"), "w") as f:
        json.dump({"days": [{"date": "2026-01-01", "visits": 1}] * 501}, f)
    with open(os.path.join(h, "data", "ok.json"), "w") as f:
        json.dump({"site": "example.test", "days": [{"date": "2026-%02d-%02d" % (i // 28 % 12 + 1, i % 28 + 1), "visits": i} for i in range(400)]}, f)
    for pid, path, t in (("huge", "huge.json", "sales"), ("deep", "deep.json", "sales"), ("rows", "rows.json", "website_visits"), ("okay", "ok.json", "website_visits")):
        cfgmod.add_panel(t, id=pid, source={"mode": "file", "path": "data/" + path}, home=h)
    t0 = time.monotonic()
    r = build(h, env=T.clean_env())
    el = time.monotonic() - t0
    st = {p["id"]: p for p in r["panels"]}
    check("source file over 2 MB: that panel errors with a clear message", st["huge"]["status"] == "error" and "larger than 2 MB" in st["huge"]["error"] and "Fix:" in st["huge"]["error"], st["huge"])
    check("absurdly nested file: clean error, no crash", st["deep"]["status"] == "error" and "not valid JSON" in st["deep"]["error"], st["deep"])
    check("more than 500 items in a list: clear error", st["rows"]["status"] == "error" and "more than 500" in st["rows"]["error"], st["rows"])
    check("a good panel next to the bad ones still builds", st["okay"]["status"] == "ok")
    d = [p for p in json.loads(read(r["data_json"]))["panels"] if p["id"] == "okay"][0]
    check("rows in the page are capped by the days option (400 rows in file -> 14 shown)", len(d["data"]["days"]) == 14, len(d["data"]["days"]))
    check("whole build with those files took under 10 s", el < 10, el)
    # huge config / key file
    with open(cfgmod.config_path(h), "a") as f:
        f.write(" " * (cfgmod.MAX_CONFIG_BYTES + 10))
    check("config.json over 256 KB is refused with a fix", T.raises(lambda: cfgmod.load(h), CE, "larger than 256 KB"))
    shutil.rmtree(h)
    h = tmp()
    cfgmod.init("Caps", home=h)
    with open(os.path.join(h, "secrets.env"), "w") as f:
        f.write("A_B_CC=" + "z" * (cfgmod.MAX_ENV_FILE_BYTES + 10) + "\n")
    os.chmod(os.path.join(h, "secrets.env"), 0o600)
    r = build(h, env=T.clean_env())
    check("key file over 64 KB is ignored with a warning (build still works)", r["ok"] and any("larger than 64 KB" in w for w in r["warnings"]), r["warnings"])
    shutil.rmtree(h)

    # ---- http caps, timeouts, no retries
    h = tmp()
    cfgmod.init("Caps", panels=[], home=h)
    for pid, path, to in (("big", "/big", 10), ("slow", "/slow", 1), ("drip", "/drip", 1), ("err", "/500", 10)):
        cfgmod.add_panel("sales", id=pid, source={"mode": "http_json", "url": base + path, "timeout": to}, home=h)
    t0 = time.monotonic()
    r = build(h, env=T.clean_env())
    el = time.monotonic() - t0
    st = {p["id"]: p for p in r["panels"]}
    check("response over 2 MB: clear error", st["big"]["status"] == "error" and "larger than 2 MB" in st["big"]["error"], st["big"])
    check("slow endpoint gives up at its timeout (1 s) with a plain error", st["slow"]["status"] == "error" and "time limit" in st["slow"]["error"], st["slow"])
    check("slow-drip endpoint (1 byte per 0.25 s) is cut at the TOTAL time limit, not per read",
          st["drip"]["status"] == "error" and "time limit" in st["drip"]["error"], st["drip"])
    check("HTTP 500: error card", st["err"]["status"] == "error" and "500" in st["err"]["error"])
    check("two 1 s timeouts + a big download finished in under 8 s total (nothing hangs)", el < 8, el)
    check("no automatic retries: each URL requested exactly once per build", all(H.hits.get(p) == 1 for p in ("/big", "/slow", "/drip", "/500")), H.hits)
    shutil.rmtree(h)

    # ---- whole-build time budget
    h = tmp()
    cfgmod.init("Caps", panels=[], home=h)
    for i in range(3):
        cfgmod.add_panel("sales", id="s%d" % i, source={"mode": "http_json", "url": base + "/slow", "timeout": 3}, home=h)
    os.environ["DASHBOARD_BUILD_TIMEOUT"] = "5"
    try:
        t0 = time.monotonic()
        r = build(h, env=T.clean_env())
        el = time.monotonic() - t0
    finally:
        del os.environ["DASHBOARD_BUILD_TIMEOUT"]
    check("build time budget: 3 panels x 3 s slow sources stop at ~5 s total", el < 7.5 and any("time budget" in p.get("error", "") for p in r["panels"]), (el, [p.get("error", "")[:60] for p in r["panels"]]))
    shutil.rmtree(h)
    srv.shutdown()

    # ---- tool answers stay small even with huge sources
    h = tmp()
    cfgmod.init("Caps", panels=[], home=h)
    os.makedirs(os.path.join(h, "data"))
    marker = "UNIQUE-DATA-MARKER-31337"
    with open(os.path.join(h, "data", "big.json"), "w") as f:
        json.dump({"bots": [{"name": marker + str(i), "status": "active", "last_action": "y" * 160} for i in range(500)], "pad": "p" * 1_800_000}, f)
    for i in range(cfgmod.MAX_PANELS):
        if i % 2:
            cfgmod.add_panel("bot_activity", id="big%d" % i, options={"max_items": 100}, source={"mode": "file", "path": "data/big.json"}, home=h)
        else:
            cfgmod.add_panel("sales", id="bad%d" % i, source={"mode": "file", "path": "data/missing-%s.json" % ("m" * 40)}, home=h)
    m = Mcp(h, {"DASHBOARD_BROWSER": "none"})
    sizes = {}
    for name, args in (("rebuild", {}), ("open_dashboard", {}), ("setup", {"no_open": True}), ("list_panels", {}), ("list_panel_library", {}), ("list_panel_library", {"type": "sales"})):
        line = m.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args}}) + "\n") and m.p.stdin.flush()
        resp = m.p.stdout.readline()
        text = json.loads(resp)["result"]["content"][0]["text"]
        sizes[name + ("(type)" if args.get("type") else "")] = (len(text.encode()), len(resp.encode()), marker in text, "<html" in text.lower())
    mx = 8 * 1024
    check("20 panels, 2 MB files, failing sources: every tool answer text is <= 8 KB", all(v[0] <= mx for v in sizes.values()), sizes)
    check("no answer carries source data or HTML back to the bot", not any(v[2] or v[3] for v in sizes.values()), sizes)
    check("list_panel_library with type is much smaller than the full list", sizes["list_panel_library(type)"][0] < sizes["list_panel_library"][0] / 3, sizes)
    err, out = m.call("rebuild")
    check("rebuild answer lists panels with short error text (<=140 chars each when many fail)", not err and all(len(p.get("error", "")) <= 140 for p in out["panels"]))
    m.close()
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import mcp_server
    check("a too-long answer is cut with a marker", len(mcp_server._text("a" * 50000)["content"][0]["text"].encode()) <= mx and "answer cut" in mcp_server._text("a" * 50000)["content"][0]["text"])
    r = build(h, env=T.clean_env())
    html_b = os.path.getsize(r["html"])
    check("page for 20 panels with the biggest allowed data stays under 8 MB", html_b < 8 * 1024 * 1024, html_b)
    shutil.rmtree(h)

    # ---- idle server: no threads, no timers, no output, no CPU
    section("14. idle server and static page")
    h = tmp()
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "mcp_server.py")], cwd=ROOT, env=T.clean_env(DASHBOARD_HOME=h), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.6)

    def cpu():
        with open("/proc/%d/stat" % p.pid) as f:
            x = f.read().rsplit(")", 1)[1].split()
        return (int(x[11]) + int(x[12])) / os.sysconf("SC_CLK_TCK"), int(x[17]), x[0]
    if os.path.exists("/proc/%d/stat" % p.pid):
        c0, t0n, _ = cpu()
        time.sleep(4)
        c1, t1n, state = cpu()
        import select
        ready = select.select([p.stdout, p.stderr], [], [], 0)[0]
        check("idle MCP server: 4 s with no calls uses <= 0.02 s CPU, 1 thread, sleeping, silent", c1 - c0 <= 0.02 and t0n == 1 and t1n == 1 and state == "S" and not ready, (c0, c1, t1n, state, ready))
    else:
        print("SKIP idle CPU check (no /proc)")
    p.stdin.close()
    p.wait(timeout=5)
    shutil.rmtree(h)
    bad = []
    for d in ("dashlib", "server", "scripts"):
        for f in os.listdir(os.path.join(ROOT, d)):
            if f.endswith(".py"):
                src = read(os.path.join(ROOT, d, f))
                for pat in (r"^\s*(import|from)\s+(threading|multiprocessing|sched|signal|asyncio|socketserver|select|selectors)\b", r"time\.sleep|\bsetitimer\b|\balarm\(|Thread\(|Timer\("):
                    if re.search(pat, src, re.M):
                        bad.append((d + "/" + f, pat))
    check("no threads, timers, signals, sleep loops or watchers in any Python file that ships", not bad, bad)
    net = [f for f in ("dashlib/build.py", "dashlib/config.py", "dashlib/library.py", "dashlib/opener.py", "server/mcp_server.py", "scripts/build.py")
           if re.search(r"urllib\.request|http\.client|import socket|requests|subprocess", read(os.path.join(ROOT, f)))]
    check("only dashlib/sources.py can touch the network (urllib.request, http_json only)", not net and "urllib.request" in read(os.path.join(ROOT, "dashlib", "sources.py")), net)
    llm = [(f, w) for dp, dn, fn in os.walk(ROOT) if "tests" not in os.path.relpath(dp, ROOT).split(os.sep) and ".git" not in dp for f in fn if f.endswith((".py", ".js", ".html", ".json"))
           for w in re.findall(r"(?i)\b(anthropic|openai|x\.ai|api\.x|chat/completions|gpt-?\d|claude|gemini|mistral|openrouter)\b|(?<!no )(?<!No )\bLLM\b", read(os.path.join(dp, f)))]
    check("no model-provider or LLM-client names anywhere in the shipped code", not llm, llm[:5])
    tpl = read(os.path.join(ROOT, "dashlib", "template.html"))
    check("page: no meta refresh, no setInterval, no fetch/XHR/WebSocket/beacon/EventSource, no external src/href/@import",
          not re.search(r"http-equiv|setInterval|\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|importScripts|@import|(src|href)=[\"']?https?:|url\(\s*[\"']?https?:", tpl, re.I))
    timers = sorted(set(re.findall(r"setTimeout|requestAnimationFrame|setInterval|requestIdleCallback", tpl)))
    check("page timers are only the drag-hold timer and drag animation frames (local UI)", timers == ["requestAnimationFrame", "setTimeout"], timers)
    docs = read(os.path.join(ROOT, "README.md")) + read(os.path.join(ROOT, "docs", "connect-a-source.md"))
    crons = re.findall(r"\*/\d+ \* \* \* \* ([^`\n]+)", docs)
    check("every cron command in the docs runs python3 .../scripts/build.py (never a bot)", crons and all("scripts/build.py" in c and not re.search(r"(?i)\bbot\b|grok|llm|curl", c) for c in crons), crons)

    # ---- listing claims match the audit
    section("15. listing claims match the usage audit")
    audit = read(os.path.join(ROOT, "docs", "usage-audit.md"))
    readme, listing = read(os.path.join(ROOT, "README.md")), read(os.path.join(ROOT, "LISTING.md"))
    pj = json.loads(read(os.path.join(ROOT, "plugin.json")))
    claim = ("Refreshing your dashboard is a plain script: no model calls and no bot turns, about 0.05 s of CPU for a 5-panel build, "
             "and the idle server uses zero CPU. Your bot is only involved when you ask it to set up, add, move or open something, "
             "each one to three small tool calls (answers capped at 8 KB, never containing your data).")
    check("README and LISTING carry the verified claim word for word", claim in readme and claim in listing)
    check("README first screen (first 25 lines) has the claim, screenshot and the 'Why you'll like it' list", claim in "\n".join(readme.splitlines()[:25]) and "docs/preview-desktop.png" in "\n".join(readme.splitlines()[:25]) and "## Why you'll like it" in readme)
    check("the numbers in the claim are in the audit (0.044 s CPU sample build, 0.00 s idle, 8 KB cap)", "0.044 s" in audit and "0.00 s" in audit and "8 KB" in audit)
    check("README and LISTING state the connector limit and the ~2K token setup cost", all("connector" in t and "2K tokens" in t and "one VM" in t for t in (readme, listing)))
    check("plugin.json description: short, says plain script / no model calls / no account", len(pj["description"]) <= 120 and "no model calls" in pj["description"] and "No account" in pj["description"])
    check("screenshots in README have alt text and relative paths", re.findall(r"!\[[^\]]+\]\(docs/", readme) and "src=\"docs/" in readme and "](/" not in readme)

    # ---- launch exactly as mcp.json says, the way two kinds of client would
    section("16. mcp.json launch (works whichever plugin-root variable the client fills in)")
    mj = json.loads(read(os.path.join(ROOT, "mcp.json")))["mcpServers"]["dashboard"]
    check("mcp.json: no ${PLUGIN_DATA}, no cwd, no env (nothing a client might leave unexpanded can break the folder)", "cwd" not in mj and "env" not in mj and "PLUGIN_DATA" not in json.dumps(mj))
    for label, subs in (("spec client (fills PLUGIN_ROOT only)", {"${PLUGIN_ROOT}": ROOT}), ("Cursor (fills CURSOR_PLUGIN_ROOT only)", {"${CURSOR_PLUGIN_ROOT}": ROOT}), ("client that fills both", {"${PLUGIN_ROOT}": ROOT, "${CURSOR_PLUGIN_ROOT}": ROOT})):
        args = [subs.get(a, a) for a in mj["args"]]
        where, hm = tmp(), tmp()
        e = T.clean_env(HOME=hm, USERPROFILE=hm, DASHBOARD_BROWSER="none")
        e.pop("DASHBOARD_HOME", None)
        e["DASHBOARD_HOME"] = "${PLUGIN_DATA}/dashboard"   # an unexpanded placeholder must not create a folder with that name
        p = subprocess.Popen([sys.executable] + args, cwd=where, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outs = []
        reqs = [("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}), ("tools/list", {}), ("tools/call", {"name": "setup", "arguments": {"panels": ["sales"], "no_open": True}})]
        for i, (meth, prm) in enumerate(reqs, 1):
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": meth, "params": prm}) + "\n")
            p.stdin.flush()
            outs.append(json.loads(p.stdout.readline()))
        p.stdin.close()
        p.wait(timeout=5)
        check("%s: server starts from another working folder and lists 10 tools" % label, len(outs[1]["result"]["tools"]) == 10)
        check("%s: setup works; a placeholder left in DASHBOARD_HOME falls back to the default folder in HOME" % label, os.path.isfile(os.path.join(hm, ".agent-dashboard", "out", "dashboard.html")) and not os.path.exists(os.path.join(where, "${PLUGIN_DATA}")), outs[2])
        shutil.rmtree(where); shutil.rmtree(hm)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_all
    test_all.run_only(run)
