#!/usr/bin/env python3
"""Usage measurements for docs/usage-audit.md. Standard library only. Not part of the pass/fail suite (numbers vary by machine).

  python3 tests/measure_usage.py [--idle SECONDS] [--runs N] [--json out.json] [section ...]
sections: static network perf idle session sizes   (default: all)
Everything runs in throw-away temp folders with an empty HOME. Nothing is published or sent anywhere."""
import argparse
import json
import os
import re
import resource
import select
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PY = sys.executable
OUT = {}


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if not re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|BEARER)", k, re.I) and k != "DASHBOARD_HOME"}
    e.update(extra)
    return e


def med(xs):
    return round(statistics.median(xs), 3)


# ------------------------------------------------------------------ 1. static scan
PATTERNS = ["anthropic", "openai", "x.ai", "grok", "llm", "completion", "gpt", "claude", "gemini", "mistral", "cohere", "openrouter", "chat/completions", "api_key", "model="]
NET_PATTERNS = ["urllib", "http.client", "socket", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "websocket", "fetch(", "XMLHttpRequest", "sendBeacon", "EventSource", "<link", "<script src", "@import", "http-equiv"]
TIMER_PATTERNS = ["threading", "Thread(", "Timer(", "sched", "signal.", "alarm", "time.sleep", "asyncio", "setInterval", "requestAnimationFrame", "setTimeout", "inotify", "watchdog", "select.", "multiprocessing", "fork(", "subprocess"]


def package_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "tests")]
        for f in fn:
            if f.endswith((".py", ".js", ".html", ".json", ".md", ".sh")):
                yield os.path.join(dp, f)


def scan(patterns, only=None):
    hits = {}
    for p in package_files():
        rel = os.path.relpath(p, ROOT)
        if only and not any(rel.startswith(o) for o in only):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for n, ln in enumerate(f, 1):
                low = ln.lower()
                for pat in patterns:
                    if re.search(r"(?<![a-z0-9])" + re.escape(pat.lower()) + r"(?![a-z0-9])", low):
                        hits.setdefault(pat, []).append("%s:%d" % (rel, n))
    return hits


def sec_static():
    code = ("dashlib/", "server/", "scripts/")
    res = {"files_scanned": sum(1 for _ in package_files()),
           "model_provider_terms_in_code": scan(PATTERNS, code),
           "model_provider_terms_whole_package": {k: v for k, v in scan(PATTERNS).items()},
           "network_apis_in_code": scan(NET_PATTERNS, code),
           "page_template_network_refs": scan(NET_PATTERNS, ("dashlib/template.html",)),
           "timer_thread_apis_in_code": scan(TIMER_PATTERNS, code),
           "timer_apis_in_page_template": scan(TIMER_PATTERNS, ("dashlib/template.html",))}
    OUT["static"] = res
    for k, v in res.items():
        print(k, "->", v if not isinstance(v, dict) else {a: b for a, b in v.items()})


# ------------------------------------------------------------------ data generators
def _pad(d, size):
    d["_pad"] = "x" * size
    return d


def write_data(home, big=False, pad=0):
    os.makedirs(os.path.join(home, "data"), exist_ok=True)
    n = 500
    docs = {
        "x.json": {"handle": "sampleco", "followers": 1000, "following": 10,
                   "posts": [{"time": "2026-10-%02d 09:00" % (i % 28 + 1), "text": "Sample post number %d " % i + "lorem ipsum " * 20, "impressions": i * 7, "likes": i, "reposts": i // 3, "replies": i // 5} for i in range(n)]},
        "web.json": {"site": "example.test", "days": [{"date": "2026-%02d-%02d" % (i // 28 % 12 + 1, i % 28 + 1), "visits": 100 + i, "page_loads": 200 + i} for i in range(n)]},
        "sales.json": {"currency": "USD", "days": [{"date": "2026-%02d-%02d" % (i // 28 % 12 + 1, i % 28 + 1), "orders": i % 9, "revenue": i * 3.5} for i in range(n)],
                       "recent": [{"date": "2026-10-01", "item": "Item " * 10, "amount": 9.5} for _ in range(n)]},
        "ads.json": {"platform": "Example Ads", "currency": "USD", "days": [{"date": "2026-%02d-%02d" % (i // 28 % 12 + 1, i % 28 + 1), "impressions": 1000 + i, "clicks": 20 + i % 7, "spend": 10 + i % 13} for i in range(n)]},
        "bots.json": {"bots": [{"name": "Bot %d" % i, "status": ["active", "idle", "paused"][i % 3], "last_update": "2026-10-23T09:00:00", "last_action": "did sample thing " * 10} for i in range(n)]},
    }
    for k, d in docs.items():
        with open(os.path.join(home, "data", k), "w") as f:
            json.dump(_pad(d, pad) if pad else d, f)
    return {"x_stats": "data/x.json", "website_visits": "data/web.json", "sales": "data/sales.json", "ads": "data/ads.json", "bot_activity": "data/bots.json"}


def make_home(kind):
    """kind: sample5 | files5 | files5_2mb | max20_2mb"""
    sys.path.insert(0, ROOT)
    from dashlib import config as c
    h = tempfile.mkdtemp(prefix="dash-meas-")
    types = ["x_stats", "website_visits", "sales", "ads", "bot_activity"]
    if kind == "sample5":
        c.init("Sampleco dashboard", home=h)
        return h
    files = write_data(h, pad=1_900_000 if "2mb" in kind else 0)
    c.init("Sampleco dashboard", panels=[], home=h)
    reps = 4 if kind.startswith("max20") else 1
    for r in range(reps):
        for t in types:
            c.add_panel(t, id="%s-%d" % (t.replace("_", "-"), r), source={"mode": "file", "path": files[t]}, home=h)
    return h


# ------------------------------------------------------------------ 2. network blocked
BLOCK_PROBE = r"""
import socket, sys, os, json
def probe():
    out = {}
    s = socket.socket(); s.settimeout(2)
    try: s.connect(("1.1.1.1", 443)); out["connect"] = "CONNECTED"
    except OSError as e: out["connect"] = "blocked (%s)" % type(e).__name__
    try: socket.getaddrinfo("example.com", 443); out["dns"] = "RESOLVED"
    except OSError as e: out["dns"] = "blocked (%s)" % type(e).__name__
    return out
print("PROBE", json.dumps(probe()))
"""
PATCH = r"""
import socket
def _no(*a, **k): raise OSError("network disabled by test")
socket.socket.connect = _no; socket.create_connection = _no; socket.getaddrinfo = _no
"""


def have_unshare():
    return subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0


def sec_network():
    h = make_home("sample5")
    env = clean_env(HOME=h, DASHBOARD_HOME=h, DASHBOARD_BROWSER="none")
    res = {}
    if have_unshare():
        pre = ["unshare", "-rn"]
        res["method"] = "unshare -rn (new user + network namespace: no interfaces except a down loopback)"
    else:
        pre = []
        res["method"] = "in-process socket monkeypatch only (unshare not available)"
    # A: CLI build
    code = BLOCK_PROBE + "\nimport runpy,sys\nsys.argv=['build.py']\nrunpy.run_path(%r, run_name='__main__')" % os.path.join(ROOT, "scripts", "build.py")
    p = subprocess.run(pre + [PY, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT)
    res["cli_build"] = {"rc": p.returncode, "probe": [l for l in p.stdout.splitlines() if l.startswith("PROBE")], "out": [l for l in p.stdout.splitlines() if l.startswith("wrote")]}
    # B: MCP server (rebuild, open_dashboard no_open) in the blocked namespace
    code = BLOCK_PROBE + "\nsys.stdout.flush()\nimport runpy\nrunpy.run_path(%r, run_name='__main__')" % os.path.join(ROOT, "server", "mcp_server.py")
    srv = subprocess.Popen(pre + [PY, "-c", code], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
    probe = srv.stdout.readline().strip()

    def rpc(i, method, params=None):
        srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
        srv.stdin.flush()
        return json.loads(srv.stdout.readline())
    rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    rb = rpc(2, "tools/call", {"name": "rebuild", "arguments": {}})["result"]
    od = rpc(3, "tools/call", {"name": "open_dashboard", "arguments": {"no_open": True}})["result"]
    srv.stdin.close()
    srv.wait(timeout=5)
    res["mcp"] = {"probe": probe, "rebuild_isError": rb["isError"], "open_dashboard_isError": od["isError"],
                  "rebuild_panels_ok": json.loads(rb["content"][0]["text"])["panels"] and all(x["status"] == "ok" for x in json.loads(rb["content"][0]["text"])["panels"])}
    # C: patch fallback always shown too
    code = PATCH + "\nimport runpy,sys\nsys.argv=['build.py']\nrunpy.run_path(%r, run_name='__main__')" % os.path.join(ROOT, "scripts", "build.py")
    p = subprocess.run([PY, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT)
    res["cli_build_socket_patched"] = {"rc": p.returncode, "out": [l for l in p.stdout.splitlines() if l.startswith("wrote")]}
    shutil.rmtree(h)
    OUT["network"] = res
    print(json.dumps(res, indent=1))


# ------------------------------------------------------------------ 3. perf
def run_child(argv, env, cwd=ROOT):
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, status, ru = os.wait4(p.pid, 0)
    wall = time.perf_counter() - t0
    return {"rc": os.waitstatus_to_exitcode(status), "wall": wall, "cpu": ru.ru_utime + ru.ru_stime, "rss_mb": ru.ru_maxrss / 1024.0}


def sec_perf(runs):
    res = {}
    base = [run_child([PY, "-c", "pass"], clean_env()) for _ in range(runs)]
    res["python_startup_only"] = {"wall_s": med([b["wall"] for b in base]), "cpu_s": med([b["cpu"] for b in base]), "rss_mb": med([b["rss_mb"] for b in base])}
    for kind in ("sample5", "files5", "files5_2mb", "max20_2mb"):
        h = make_home(kind)
        sizes = sum(os.path.getsize(os.path.join(h, "data", f)) for f in os.listdir(os.path.join(h, "data"))) if os.path.isdir(os.path.join(h, "data")) else 0
        env = clean_env(HOME=h, DASHBOARD_HOME=h)
        rs = [run_child([PY, os.path.join(ROOT, "scripts", "build.py")], env) for _ in range(runs)]
        assert all(r["rc"] == 0 for r in rs), (kind, rs)
        panels = len(json.load(open(os.path.join(h, "config.json")))["panels"])
        outsz = {n: os.path.getsize(os.path.join(h, "out", n)) for n in ("dashboard.html", "data.json")}
        res[kind] = {"panels": panels, "input_bytes": sizes, "runs": runs, "wall_s": med([r["wall"] for r in rs]), "cpu_s": med([r["cpu"] for r in rs]),
                     "peak_rss_mb": med([r["rss_mb"] for r in rs]), "max_wall_s": round(max(r["wall"] for r in rs), 3), "html_bytes": outsz["dashboard.html"], "data_json_bytes": outsz["data.json"]}
        shutil.rmtree(h)
        print(kind, res[kind])
    OUT["perf"] = res
    print(json.dumps(res, indent=1))


# ------------------------------------------------------------------ 4. idle
def proc_stat(pid):
    with open("/proc/%d/stat" % pid) as f:
        parts = f.read().rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return {"state": parts[0], "utime_s": int(parts[11]) / ticks, "stime_s": int(parts[12]) / ticks, "threads": int(parts[17])}


def sec_idle(seconds):
    h = tempfile.mkdtemp(prefix="dash-idle-")
    env = clean_env(HOME=h, DASHBOARD_HOME=h)
    p = subprocess.Popen([PY, os.path.join(ROOT, "server", "mcp_server.py")], env=env, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    start = proc_stat(p.pid)
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}) + "\n")
    p.stdin.flush()
    p.stdout.readline()
    after_init = proc_stat(p.pid)
    t0 = time.time()
    samples = []
    while time.time() - t0 < seconds:
        time.sleep(min(10, seconds))
        samples.append(proc_stat(p.pid))
    end = samples[-1]
    r, _, _ = select.select([p.stdout, p.stderr], [], [], 0)
    with open("/proc/%d/status" % p.pid) as f:
        st = f.read()
    ctx = {k: int(v) for k, v in re.findall(r"(voluntary_ctxt_switches|nonvoluntary_ctxt_switches):\s+(\d+)", st)}
    res = {"idle_seconds": seconds, "state_when_idle": end["state"], "threads_at_start": start["threads"], "threads_at_end": end["threads"],
           "cpu_after_startup_s": round(start["utime_s"] + start["stime_s"], 3), "cpu_after_initialize_s": round(after_init["utime_s"] + after_init["stime_s"], 3),
           "cpu_at_end_s": round(end["utime_s"] + end["stime_s"], 3), "cpu_used_during_idle_s": round((end["utime_s"] + end["stime_s"]) - (after_init["utime_s"] + after_init["stime_s"]), 3),
           "output_while_idle": bool(r), "ctx_switches": ctx, "open_child_processes": len(subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split())}
    p.stdin.close()
    p.wait(timeout=5)
    shutil.rmtree(h)
    OUT["idle"] = res
    print(json.dumps(res, indent=1))


# ------------------------------------------------------------------ 5. session bytes
class Counting:
    def __init__(self, env):
        self.p = subprocess.Popen([PY, os.path.join(ROOT, "server", "mcp_server.py")], env=env, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0
        self.log = []

    def rpc(self, label, method, params=None):
        self.n += 1
        req = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        self.p.stdin.write(req + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        self.log.append((label, len(req.encode()), len(line.encode())))
        return json.loads(line)

    def tool(self, name, args=None):
        r = self.rpc(name, "tools/call", {"name": name, "arguments": args or {}})
        return r["result"]["isError"], r["result"]["content"][0]["text"]


def sec_session():
    h = tempfile.mkdtemp(prefix="dash-sess-")
    c = Counting(clean_env(HOME=h, USERPROFILE=h, DASHBOARD_BROWSER="none"))
    c.rpc("initialize", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "m", "version": "1"}})
    c.rpc("tools/list", "tools/list")
    mark = len(c.log)
    c.tool("setup", {"title": "Sampleco dashboard", "panels": ["x_stats", "website_visits", "sales", "ads", "bot_activity"], "no_open": True})
    setup = c.log[mark:]
    mark = len(c.log)
    c.tool("add_panel", {"type": "sales", "id": "sales-2"})
    c.tool("open_dashboard", {"no_open": True})
    add = c.log[mark:]
    mark = len(c.log)
    c.tool("reorder_panels", {"order": ["sales", "sales-2", "x-stats", "website-visits", "ads", "bot-activity"]})
    c.tool("open_dashboard", {"no_open": True})
    reorder = c.log[mark:]
    mark = len(c.log)
    c.tool("open_dashboard", {"no_open": True})
    opened = c.log[mark:]
    mark = len(c.log)
    c.tool("list_panels")
    c.tool("remove_panel", {"id": "sales-2"})
    c.tool("open_dashboard", {"no_open": True})
    remove = c.log[mark:]
    mark = len(c.log)
    c.tool("list_panel_library")
    lib = c.log[mark:]
    c.p.stdin.close()
    c.p.wait(timeout=5)
    shutil.rmtree(h)

    def tot(rows):
        return {"calls": len(rows), "request_bytes": sum(r[1] for r in rows), "response_bytes": sum(r[2] for r in rows), "per_call": [{"call": r[0], "req": r[1], "resp": r[2]} for r in rows]}
    res = {"handshake_initialize_and_tools_list": tot(c.log[:2]), "first_run_setup_5_panels": tot(setup), "add_one_panel_and_show": tot(add),
           "reorder_and_show": tot(reorder), "open_my_dashboard": tot(opened), "remove_one_panel_and_show (incl. list_panels)": tot(remove), "list_panel_library_(optional lookup)": tot(lib)}
    OUT["session"] = res
    for k, v in res.items():
        print(k, {a: b for a, b in v.items() if a != "per_call"}, v["per_call"])


# ------------------------------------------------------------------ 6. sizes
def sec_sizes():
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import mcp_server as m
    tl = json.dumps({"tools": m.TOOLS}, separators=(",", ":"))
    res = {"tools_list_bytes": len(tl.encode()), "tools_list_approx_tokens": len(tl.encode()) // 4, "tool_count": len(m.TOOLS),
           "tool_description_bytes": sum(len(t["description"].encode()) for t in m.TOOLS), "skills": {}}
    for d in sorted(os.listdir(os.path.join(ROOT, "skills"))):
        raw = open(os.path.join(ROOT, "skills", d, "SKILL.md"), encoding="utf-8").read()
        desc = re.search(r"^description: (.+)$", raw, re.M).group(1)
        res["skills"][d] = {"bytes": len(raw.encode()), "lines": raw.count("\n"), "approx_tokens": len(raw.encode()) // 4, "description_bytes": len(desc.encode())}
    res["skills_total"] = {"bytes": sum(s["bytes"] for s in res["skills"].values()), "lines": sum(s["lines"] for s in res["skills"].values()),
                           "approx_tokens": sum(s["approx_tokens"] for s in res["skills"].values()), "description_bytes_always_loaded": sum(s["description_bytes"] for s in res["skills"].values())}
    OUT["sizes"] = res
    print(json.dumps(res, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=int, default=45)
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--json")
    ap.add_argument("sections", nargs="*")
    a = ap.parse_args()
    want = a.sections or ["static", "network", "perf", "idle", "session", "sizes"]
    for s in want:
        print("\n=== %s" % s)
        {"static": sec_static, "network": sec_network, "perf": lambda: sec_perf(a.runs), "idle": lambda: sec_idle(a.idle), "session": sec_session, "sizes": sec_sizes}[s]()
    if a.json:
        with open(a.json, "w") as f:
            json.dump(OUT, f, indent=1)


if __name__ == "__main__":
    main()
