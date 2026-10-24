"""Brand-new-user tests: an empty home folder, a scripted MCP session, then every failure path.
Run as part of tests/test_all.py (run.sh). `python3 tests/test_first_run.py` runs just this file."""
import json
import os
import shutil
import socket
import subprocess
import sys

sys.dont_write_bytecode = True


def run(T):
    check, section, Mcp, tmp, read = T.check, T.section, T.Mcp, T.tmp, T.read
    ROOT = T.ROOT

    def fresh_home():
        return tmp()

    def server(home_dir, **extra):
        env = {"HOME": home_dir, "USERPROFILE": home_dir, "DASHBOARD_BROWSER": "none"}
        env.update(extra)
        return Mcp(None, env)  # no DASHBOARD_HOME: the default folder under HOME is what a new user gets

    # ---------------------------------------------------------------- A. first run, happy path
    section("9. first run as a brand-new user (empty HOME, scripted MCP session)")
    user = fresh_home()
    folder = os.path.join(user, ".agent-dashboard")
    check("starting state: nothing exists", os.listdir(user) == [])
    m = server(user)
    m.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    names = [t["name"] for t in m.rpc("tools/list")["result"]["tools"]]
    check("setup tool is offered", "setup" in names and "open_dashboard" in names, names)
    err, out = m.call("list_panels")
    check("before setup: plain-word error says 'set up my dashboard'", err and "set up my dashboard" in out and "Fix:" in out, out)
    check("a read-only call created nothing", not os.path.exists(os.path.join(folder, "config.json")))
    err, r = m.call("setup", {"title": "Sampleco dashboard", "panels": ["x_stats", "sales"], "no_open": True})
    check("setup from nothing works", not err and r["created"] is True, r)
    check("default folder is HOME-relative and was created automatically", os.path.realpath(r["dashboard_folder"]) == os.path.realpath(folder) and os.path.isdir(folder))
    check("data/ folder created for exports", os.path.isdir(os.path.join(folder, "data")))
    check("chosen panels only, sample mode", [p["type"] for p in r["panels"]] == ["x_stats", "sales"] and all(p["source_mode"] == "sample" for p in r["panels"]), r["panels"])
    check("page built with both panels ok", os.path.isfile(r["page"]) and [b["status"] for b in r["build"]] == ["ok", "ok"] and r["opened"] is False)
    html = read(r["page"])
    check("page carries the title and the SAMPLE marking", "Sampleco dashboard" in html and "SAMPLE" in html)
    check("setup suggests the next prompts", any("move sales" in x for x in r["suggest"]) and any("open my dashboard" in x for x in r["suggest"]))
    check("keys file location is reported (not created)", r["keys_file"].endswith("secrets.env") and not os.path.exists(r["keys_file"]))

    err, r2 = m.call("setup", {"panels": ["x_stats", "ads"], "no_open": True})
    ids = [p["id"] for p in r2["panels"]]
    check("setup again is idempotent: only the missing panel is added", not err and r2["created"] is False and r2["added"] == ["ads"] and ids == ["x-stats", "sales", "ads"], r2)
    err, r3 = m.call("setup", {"no_open": True})
    check("setup with no arguments on an existing dashboard just rebuilds", not err and r3["added"] == [] and len(r3["panels"]) == 3)
    err, out = m.call("setup", {"panels": ["crypto"]})
    check("setup with an unknown panel type -> error listing the real types + Fix", err and "x_stats" in out and "Fix:" in out and "internal error" not in out, out)
    err, out = m.call("add_panel", {"type": "nope"})
    check("add_panel unknown type -> same plain-word error", err and "available:" in out and "Fix:" in out, out)

    err, out = m.call("add_panel", {"type": "website_visits"})
    check("add a panel", not err and out["id"] == "website-visits")
    err, out = m.call("reorder_panels", {"order": ["sales", "website-visits", "x-stats", "ads"]})
    check("reorder", not err, out)
    err, out = m.call("remove_panel", {"id": "ads"})
    check("remove", not err, out)
    err, out = m.call("rebuild")
    check("rebuild after edits: 3 panels ok", not err and [p["id"] for p in out["panels"]] == ["sales", "website-visits", "x-stats"] and all(p["status"] == "ok" for p in out["panels"]), out)
    d = json.loads(read(os.path.join(folder, "out", "data.json")))
    check("data.json shows the new order", [p["id"] for p in d["panels"]] == ["sales", "website-visits", "x-stats"])

    err, out = m.call("open_dashboard", {"no_open": True})
    check("open_dashboard no_open: built, not opened, no hint needed", not err and out["opened"] is False and "hint" not in out and out["url"].startswith("file://"))
    err, out = m.call("open_dashboard")
    check("open_dashboard with no browser available: still built, opened=false, plain-word hint with the path",
          not err and out["opened"] is False and out["path"] in out["hint"] and "open this file yourself" in out["hint"], out)
    m.close()

    u2 = fresh_home()
    m2 = server(u2)
    err, r = m2.call("setup", {"no_open": True})
    check("setup with no arguments on a new machine adds all five panels", not err and len(r["panels"]) == 5 and r["created"], r)
    m2.close()
    cli_env = T.clean_env(HOME=fresh_home(), USERPROFILE=fresh_home())
    cli_env.pop("DASHBOARD_HOME", None)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], env=cli_env, capture_output=True, text=True)
    check("cron/CLI before setup: exit 2 with plain-word fix, no traceback", p.returncode == 2 and "set up my dashboard" in p.stderr and "Traceback" not in p.stderr, p.stderr)
    shutil.rmtree(u2, ignore_errors=True)

    # ---------------------------------------------------------------- B. failure paths
    section("10. failure paths: every error carries a plain-word fix")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # nothing listens here any more
    m = server(user)
    err, out = m.call("add_panel", {"type": "sales", "id": "live", "source": {"mode": "http_json", "url": "http://127.0.0.1:%d/x" % dead_port}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("unreachable endpoint: error card says so + Fix, other panels fine",
          live["status"] == "error" and "could not reach" in live["error"] and "Fix:" in live["error"] and sum(p["status"] == "ok" for p in b["panels"]) == 3, live)

    T.Handler.token = "first-run-token-VALUE-123456"
    T.Handler.payload = lambda path: T.sales_payload()
    srv = T.serve()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    m.call("update_panel", {"id": "live", "source": {"mode": "http_json", "url": base + "/sales", "auth_env": "FIRSTRUN_API_TOKEN"}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("missing env var: names the variable, says where to set it, never asks for the key in chat",
          live["status"] == "error" and "FIRSTRUN_API_TOKEN is not set" in live["error"] and "secrets.env" in live["error"] and "Never paste the key into chat" in live["error"], live)
    m.close()

    m = server(user, FIRSTRUN_API_TOKEN="definitely-the-wrong-key-1")
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("bad key (HTTP 401): says the key was rejected + Fix, no key value in output",
          live["status"] == "error" and "401" in live["error"] and "rejected" in live["error"] and "Fix:" in live["error"] and "wrong-key" not in json.dumps(b), live)
    m.close()

    # key file supplies the key; real env is not needed
    kf = os.path.join(folder, "secrets.env")
    with open(kf, "w") as f:
        f.write("# my keys\nexport FIRSTRUN_API_TOKEN='%s'\n" % T.Handler.token)
    os.chmod(kf, 0o644)
    m = server(user)
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("key from the local secrets.env file works (env var not set)", not err and live["status"] == "ok", live)
    check("loose key-file permissions produce a warning", os.name != "posix" or any("chmod 600" in w for w in b["warnings"]), b.get("warnings"))
    os.chmod(kf, 0o600)
    err, b = m.call("rebuild")
    check("no warning once the key file is private", not b["warnings"], b["warnings"])
    page = read(os.path.join(folder, "out", "dashboard.html")) + read(os.path.join(folder, "out", "data.json"))
    check("key value from the key file is not in html or data.json", T.Handler.token not in page)
    # data that echoes the key file's value is stopped by the guard, even if the name does not look secret
    with open(kf, "w") as f:
        f.write("MY_PLAIN_NAME=plain-valued-secret-98765\nFIRSTRUN_API_TOKEN=%s\n" % T.Handler.token)
    os.chmod(kf, 0o600)
    T.Handler.payload = lambda path: T.sales_payload(note=" plain-valued-secret-98765")
    before = read(os.path.join(folder, "out", "dashboard.html"))
    err, out = m.call("rebuild")
    check("leak guard covers every name in secrets.env; message names it, never the value",
          err and "SECRET LEAK GUARD" in out and "MY_PLAIN_NAME" in out and "plain-valued-secret" not in out, out)
    check("nothing was written by the blocked build", read(os.path.join(folder, "out", "dashboard.html")) == before)
    T.Handler.payload = lambda path: T.sales_payload()
    srv.shutdown()
    os.unlink(kf)

    # file sources: CSV, missing file, broken JSON, key file refused
    os.makedirs(os.path.join(folder, "data"), exist_ok=True)
    with open(os.path.join(folder, "data", "sales.csv"), "w") as f:
        f.write("date,orders,revenue\n2026-10-01,3,\"1,120.50\"\n2026-10-02,5,200\n")
    m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/sales.csv"}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    d = json.loads(read(os.path.join(folder, "out", "data.json")))
    lv = [p for p in d["panels"] if p["id"] == "live"][0]
    check("CSV file source builds, numbers with thousands commas are read", live["status"] == "ok" and abs(lv["data"]["totals"]["revenue"] - 1320.5) < 0.01 and lv["data"]["totals"]["orders"] == 8, lv.get("data", lv))
    m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/missing.json"}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("missing file: plain-word fix", live["status"] == "error" and "not found" in live["error"] and "Fix:" in live["error"], live)
    with open(os.path.join(folder, "data", "bad.json"), "w") as f:
        f.write("<html>502 Bad Gateway</html>")
    m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/bad.json"}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("file that is not JSON: plain-word fix", live["status"] == "error" and "not valid JSON" in live["error"] and "Fix:" in live["error"], live)
    with open(os.path.join(folder, "data", "wrongshape.json"), "w") as f:
        f.write('{"days": "nope"}')
    m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/wrongshape.json"}})
    err, b = m.call("rebuild")
    live = [p for p in b["panels"] if p["id"] == "live"][0]
    check("wrong data shape: error card with Fix, not a crash", live["status"] == "error" and "Fix:" in live["error"], live)
    err, out = m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/secrets.env"}})
    check("a key file cannot be used as a data file", err and "key file" in out, out)
    err, out = m.call("update_panel", {"id": "live", "source": {"mode": "file", "path": "data/x.xml"}})
    check("only .json/.csv data files accepted, with Fix", err and ".json or .csv" in out and "Fix:" in out, out)
    m.call("update_panel", {"id": "live", "source": {"mode": "sample"}})
    m.close()

    # corrupt config
    cfgp = os.path.join(folder, "config.json")
    good = read(cfgp)
    m = server(user)
    with open(cfgp, "w") as f:
        f.write("{ this is not json")
    err, out = m.call("list_panels")
    check("corrupt config with a backup: says so and names config.json.bak", err and "not valid JSON" in out and "config.json.bak" in out and "Fix:" in out and "Traceback" not in out, out)
    err, out = m.call("open_dashboard", {"no_open": True})
    check("corrupt config: open_dashboard also fails cleanly", err and "Fix:" in out, out)
    shutil.copy(cfgp + ".bak", cfgp)
    err, out = m.call("list_panels")
    check("restoring config.json.bak recovers the dashboard", not err and len(out["panels"]) >= 3, out)
    os.unlink(cfgp + ".bak")
    with open(cfgp, "w") as f:
        f.write("[]]")
    err, out = m.call("list_panels")
    check("corrupt config without a backup: points to 'set up my dashboard'", err and "set up my dashboard" in out, out)
    with open(cfgp, "w") as f:
        f.write(json.dumps({"version": 1, "title": "x", "panels": [{"id": "a", "type": "crypto"}]}))
    err, out = m.call("list_panels")
    check("hand-edited config with an unknown panel type: names the available types", err and "available:" in out and "Fix:" in out, out)
    with open(cfgp, "w") as f:
        f.write(good)
    m.close()

    # unwritable folder
    blocker = os.path.join(user, "iam-a-file")
    open(blocker, "w").close()
    m = Mcp(os.path.join(blocker, "dash"), {"DASHBOARD_BROWSER": "none"})
    err, out = m.call("setup", {"no_open": True})
    check("unwritable folder (parent is a file): clear message with DASHBOARD_HOME fix, no crash",
          err and "cannot write" in out and "DASHBOARD_HOME" in out and "internal error" not in out and "Traceback" not in out, out)
    err, out = m.call("add_panel", {"type": "sales"})
    check("unwritable folder: other tools fail the same clean way", err and "Fix:" in out and "internal error" not in out, out)
    m.close()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        ro = os.path.join(user, "readonly")
        os.makedirs(ro)
        os.chmod(ro, 0o500)
        m = Mcp(os.path.join(ro, "dash"), {"DASHBOARD_BROWSER": "none"})
        err, out = m.call("setup", {"no_open": True})
        check("read-only folder: clear message", err and "cannot write" in out and "Fix:" in out, out)
        m.close()
        os.chmod(ro, 0o700)

    # ---------------------------------------------------------------- C. docs carry the fixes
    section("11. docs and listing")
    readme, listing, conn = read(os.path.join(ROOT, "README.md")), read(os.path.join(ROOT, "LISTING.md")), read(os.path.join(ROOT, "docs", "connect-a-source.md"))
    for phrase in ("set up my dashboard", "add my X stats", "open my dashboard", "move sales to the top"):
        check("README getting-started has the prompt: %s" % phrase, phrase in readme)
    check("README: 'If something fails' section", "## If something fails" in readme)
    check("README: python missing -> install commands for macOS, Windows, Linux + version check",
          all(x in readme for x in ("brew install python", "winget install Python.Python", "apt install python3", "python3 --version", "python.org")))
    check("README: unreachable source, key missing/wrong, blank page, refresh + cron",
          all(x in readme for x in ("could not reach the source", "HTTP 401", "The page is blank", "refresh my dashboard", "cron")))
    check("connect-a-source covers connector, file (JSON/CSV), endpoint, key locations",
          all(x in conn for x in ("existing connector", "CSV", "http_json", "secrets.env", "environment variable", "never in chat")) or all(x in conn for x in ("existing connector", "CSV", "http_json", "secrets.env", "environment variable")))
    check("add-dashboard-source skill points to the doc and the key rules", all(x in read(os.path.join(ROOT, "skills", "add-dashboard-source", "SKILL.md")) for x in ("docs/connect-a-source.md", "secrets.env", "connector")))
    check("setup-my-dashboard skill exists and uses the setup tool", "`setup`" in read(os.path.join(ROOT, "skills", "setup-my-dashboard", "SKILL.md")))
    pj = json.loads(read(os.path.join(ROOT, "plugin.json")))
    check("plugin.json description is short (<=120)", 0 < len(pj["description"]) <= 120 and "version" in pj, len(pj["description"]))
    short = [l for l in listing.splitlines() if l.startswith("**Short description")][0].split("):**", 1)[1].strip()
    check("LISTING.md: short description <=120 chars and matches plugin.json", 0 < len(short) <= 120 and short == pj["description"], len(short))
    check("LISTING.md: name, long description, tags, install line, screenshots", all(x in listing for x in ("**Name:**", "**Long description:**", "**Tags:**", "**Install line:**", "docs/preview-desktop.png")))
    pngs = ["preview-desktop.png", "preview-layout-menu.png", "preview-mobile.png", "preview-mobile-full.png"]
    check("four screenshots exist, are PNGs, and are referenced from README/LISTING",
          all(open(os.path.join(ROOT, "docs", n), "rb").read(8) == b"\x89PNG\r\n\x1a\n" for n in pngs) and all(n in readme + listing for n in pngs))
    shutil.rmtree(user, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_all
    test_all.run_only(run)
