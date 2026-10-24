"""Panel library: one entry per ready-made panel type.

Each type defines:
  options      allowed options with type/limits/default (validated, unknown keys rejected)
  sample(rng, opts, today)   fictional raw data in the type's RAW shape (never real data)
  normalize(raw, opts, now)  validates + coerces raw data (from sample, file or http_json) into the
                             DISPLAY shape the template renders. Raises ValueError with a clear message.
  real_source  documented shape + how to feed it from a real system (keys come from env vars only)

RAW shapes (what a file / http_json source must return) are documented in each entry's `raw_shape`.
"""
import datetime
import re

MAX_ITEMS = 500


# ---------- small coercion helpers ----------
def _clean(s, n=200):
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(s if s is not None else ""))
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", s)
    return s.strip()[:n]


def _num(v, where, allow_none=False, integer=False, minimum=0):
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("%s must be a number (got %r)" % (where, v if not isinstance(v, str) else v[:20]))
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("%s must be finite" % where)
    if minimum is not None and v < minimum:
        raise ValueError("%s must be >= %s" % (where, minimum))
    return int(v) if integer else round(float(v), 4)


def _date(v, where):
    try:
        return datetime.date.fromisoformat(str(v)[:10]).isoformat()
    except Exception:
        raise ValueError("%s must be an ISO date like 2026-01-31" % where)


def _list(v, where, required=True):
    if v is None and not required:
        return []
    if not isinstance(v, list):
        raise ValueError("%s must be a list" % where)
    if len(v) > MAX_ITEMS:
        raise ValueError("%s has more than %d items" % (where, MAX_ITEMS))
    return v


def _obj(v, where):
    if not isinstance(v, dict):
        raise ValueError("%s must be an object" % where)
    return v


def _dt(v, where):
    try:
        d = datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        raise ValueError("%s must be an ISO datetime like 2026-01-31T12:00:00Z" % where)
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _days(today, n):
    return [(today - datetime.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _tail(items, n):
    return items[-n:] if n else items


# ---------- x_stats ----------
def _x_sample(rng, o, today):
    texts = ["Shipped a small update today", "Three tips for faster builds", "Behind the scenes: our sample week",
             "Question of the day: what are you building?", "New sample post about dashboards", "Quick thread on automation",
             "Release notes for the example product", "Sample poll: tabs or spaces?"]
    posts = []
    for i in range(o["max_posts"]):
        t = datetime.datetime.combine(today, datetime.time(9)) - datetime.timedelta(hours=11 * (i + 1))
        imp = rng.randint(300, 4200)
        posts.append({"time": t.strftime("%Y-%m-%d %H:%M"), "text": texts[i % len(texts)] + " #%d" % (i + 1),
                      "impressions": imp, "likes": imp // rng.randint(25, 60), "reposts": imp // rng.randint(90, 200),
                      "replies": imp // rng.randint(120, 300)})
    return {"handle": o["handle"], "followers": rng.randint(900, 1400), "following": rng.randint(80, 300), "posts": posts}


def _x_norm(raw, o, now):
    raw = _obj(raw, "data")
    posts = []
    for i, p in enumerate(_list(raw.get("posts"), "posts", required=False)):
        p = _obj(p, "posts[%d]" % i)
        posts.append({"time": _clean(p.get("time", ""), 32), "text": _clean(p.get("text", ""), 280),
                      "impressions": _num(p.get("impressions", 0), "posts[%d].impressions" % i, integer=True),
                      "likes": _num(p.get("likes", 0), "posts[%d].likes" % i, integer=True),
                      "reposts": _num(p.get("reposts", 0), "posts[%d].reposts" % i, integer=True),
                      "replies": _num(p.get("replies", 0), "posts[%d].replies" % i, integer=True)})
    posts = posts[: o["max_posts"]]  # newest first, as given
    tot = {k: sum(p[k] for p in posts) for k in ("impressions", "likes", "reposts", "replies")}
    tot["n"] = len(posts)
    series = [{"label": p["time"] or "#%d" % (i + 1), "impressions": p["impressions"]} for i, p in enumerate(reversed(posts))]
    return {"handle": _clean(raw.get("handle", o["handle"]), 40).lstrip("@"),
            "followers": _num(raw.get("followers"), "followers", allow_none=True, integer=True),
            "following": _num(raw.get("following"), "following", allow_none=True, integer=True),
            "posts": posts, "totals": tot, "series": series}


# ---------- website_visits ----------
def _web_sample(rng, o, today):
    base = rng.randint(120, 260)
    return {"site": o["site"], "days": [{"date": d, "visits": max(5, int(base + rng.randint(-60, 90))), "page_loads": 0} for d in _days(today, o["days"])]}


def _web_norm(raw, o, now):
    raw = _obj(raw, "data")
    days = []
    for i, d in enumerate(_list(raw.get("days"), "days")):
        d = _obj(d, "days[%d]" % i)
        v = _num(d.get("visits"), "days[%d].visits" % i, integer=True)
        pl = d.get("page_loads")
        days.append({"date": _date(d.get("date"), "days[%d].date" % i), "visits": v,
                     "page_loads": _num(pl, "days[%d].page_loads" % i, integer=True) if pl else int(v * 1.6)})
    days = _tail(sorted(days, key=lambda x: x["date"]), o["days"])
    tv = sum(d["visits"] for d in days)
    best = max(days, key=lambda d: d["visits"]) if days else None
    return {"site": _clean(raw.get("site", o["site"]), 80), "days": days, "total_visits": tv,
            "total_loads": sum(d["page_loads"] for d in days), "avg_per_day": round(tv / len(days), 1) if days else None,
            "best_day": {"date": best["date"], "visits": best["visits"]} if best else None}


# ---------- sales ----------
def _sales_sample(rng, o, today):
    items = ["Starter plan", "Pro plan", "Sticker pack", "Template bundle", "Consulting hour", "Add-on credits"]
    days, recent = [], []
    for d in _days(today, o["days"]):
        n = rng.randint(0, 6)
        amts = [round(rng.choice([9, 19, 29, 49, 99]) + rng.choice([0, 0.5]), 2) for _ in range(n)]
        days.append({"date": d, "orders": n, "revenue": round(sum(amts), 2)})
        recent += [{"date": d, "item": rng.choice(items), "amount": a} for a in amts]
    return {"currency": o["currency"], "days": days, "recent": recent[-8:][::-1]}


def _sales_norm(raw, o, now):
    raw = _obj(raw, "data")
    days = []
    for i, d in enumerate(_list(raw.get("days"), "days")):
        d = _obj(d, "days[%d]" % i)
        days.append({"date": _date(d.get("date"), "days[%d].date" % i),
                     "orders": _num(d.get("orders", 0), "days[%d].orders" % i, integer=True),
                     "revenue": _num(d.get("revenue", 0), "days[%d].revenue" % i)})
    days = _tail(sorted(days, key=lambda x: x["date"]), o["days"])
    recent = []
    for i, r in enumerate(_list(raw.get("recent"), "recent", required=False)[:20]):
        r = _obj(r, "recent[%d]" % i)
        recent.append({"date": _date(r.get("date"), "recent[%d].date" % i), "item": _clean(r.get("item", ""), 80),
                       "amount": _num(r.get("amount"), "recent[%d].amount" % i)})
    cur = _clean(raw.get("currency", o["currency"]), 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise ValueError("currency must be a 3-letter code like USD")
    orders = sum(d["orders"] for d in days)
    rev = round(sum(d["revenue"] for d in days), 2)
    return {"currency": cur, "days": days, "recent": recent,
            "totals": {"orders": orders, "revenue": rev, "avg_order": round(rev / orders, 2) if orders else None}}


# ---------- ads ----------
def _ads_sample(rng, o, today):
    days = []
    for d in _days(today, o["days"]):
        imp = rng.randint(800, 3000)
        clk = int(imp * rng.uniform(0.008, 0.03))
        days.append({"date": d, "impressions": imp, "clicks": clk, "spend": round(clk * rng.uniform(0.35, 0.9), 2)})
    return {"platform": o["platform"], "currency": o["currency"], "days": days}


def _ads_norm(raw, o, now):
    raw = _obj(raw, "data")
    days = []
    for i, d in enumerate(_list(raw.get("days"), "days")):
        d = _obj(d, "days[%d]" % i)
        days.append({"date": _date(d.get("date"), "days[%d].date" % i),
                     "impressions": _num(d.get("impressions", 0), "days[%d].impressions" % i, integer=True),
                     "clicks": _num(d.get("clicks", 0), "days[%d].clicks" % i, integer=True),
                     "spend": _num(d.get("spend", 0), "days[%d].spend" % i)})
    days = _tail(sorted(days, key=lambda x: x["date"]), o["days"])
    imp, clk = sum(d["impressions"] for d in days), sum(d["clicks"] for d in days)
    sp = round(sum(d["spend"] for d in days), 2)
    cur = _clean(raw.get("currency", o["currency"]), 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise ValueError("currency must be a 3-letter code like USD")
    return {"platform": _clean(raw.get("platform", o["platform"]), 40), "currency": cur, "days": days,
            "totals": {"impressions": imp, "clicks": clk, "spend": sp,
                       "ctr": round(100.0 * clk / imp, 2) if imp else None, "cpc": round(sp / clk, 2) if clk else None}}


# ---------- bot_activity ----------
def _bots_sample(rng, o, today):
    now = datetime.datetime.combine(today, datetime.time(12), tzinfo=datetime.timezone.utc)
    rows = [("Scout", "active", "Checked 14 sample sources"), ("Writer", "active", "Drafted 2 sample posts"),
            ("Archivist", "idle", "Filed the weekly sample report"), ("Courier", "idle", "Delivered sample digest"),
            ("Watcher", "paused", "Paused by owner (sample)"), ("Builder", "active", "Rebuilt the sample dashboard")]
    return {"bots": [{"name": n, "status": s, "last_update": (now - datetime.timedelta(minutes=rng.randint(3, 2400))).isoformat(),
                      "last_action": a} for n, s, a in rows]}


def _bots_norm(raw, o, now):
    raw = _obj(raw, "data")
    out = []
    for i, b in enumerate(_list(raw.get("bots"), "bots")):
        b = _obj(b, "bots[%d]" % i)
        st = _clean(b.get("status", "unknown"), 20).lower() or "unknown"
        lu = b.get("last_update")
        age, stale, ts = "", False, 0
        if lu:
            dt = _dt(lu, "bots[%d].last_update" % i)
            hrs = max(0.0, (now - dt).total_seconds() / 3600.0)
            age = "%dm ago" % int(hrs * 60) if hrs < 1 else ("%dh ago" % int(hrs) if hrs < 48 else "%dd ago" % int(hrs // 24))
            stale = st == "active" and hrs > o["stale_after_hours"]
            ts = dt.timestamp()
        out.append({"name": _clean(b.get("name", "?"), 60), "status": st, "age": age, "stale": stale, "ts": ts,
                    "last_action": _clean(b.get("last_action", ""), 170)})
    order = {"active": 0, "idle": 1, "paused": 2}
    out.sort(key=lambda b: (order.get(b["status"], 3), -b["ts"]))
    out = out[: o["max_items"]]
    counts = {k: sum(1 for b in out if b["status"] == k) for k in order}
    counts["other"] = len(out) - sum(counts.values())
    return {"bots": out, "counts": counts}


def _opt(kind, default, **kw):
    d = {"kind": kind, "default": default}
    d.update(kw)
    return d


COMMON_OPTIONS = {"span": _opt("int", 1, min=1, max=2, doc="grid columns the card spans (1 or 2)")}

LIBRARY = {
    "x_stats": {
        "label": "X stats", "description": "Followers plus recent post impressions, likes, reposts, replies.",
        "default_title": "X post stats", "default_span": 2,
        "options": {"handle": _opt("handle", "example_handle", doc="public handle shown in the header (no @)"),
                    "max_posts": _opt("int", 8, min=1, max=50, doc="recent posts to show")},
        "raw_shape": {"handle": "string", "followers": "int", "following": "int|null",
                      "posts": [{"time": "string", "text": "string", "impressions": "int", "likes": "int", "reposts": "int", "replies": "int"}]},
        "real_source": "X API v2: the users lookup endpoint (public_metrics) and the user tweets endpoint (tweet.fields=public_metrics,created_at). "
                       "Run your own small script (cron) with the bearer token in env X_BEARER_TOKEN that writes the raw shape to a local JSON file, "
                       "then set source {mode:file,path:data/x.json}. The dashboard never calls the X API itself.",
        "csv_list_key": "posts",
        "sample": _x_sample, "normalize": _x_norm},
    "website_visits": {
        "label": "Website visits", "description": "Daily visits and page loads for one site.",
        "default_title": "Website visits", "default_span": 1,
        "options": {"site": _opt("text", "example.test", max=80, doc="site name shown in the header"),
                    "days": _opt("int", 14, min=3, max=90, doc="days to show")},
        "raw_shape": {"site": "string", "days": [{"date": "YYYY-MM-DD", "visits": "int", "page_loads": "int (optional)"}]},
        "real_source": "Any analytics export (Plausible Stats API, Cloudflare Web Analytics GraphQL, server logs). Token in an env var, "
                       "e.g. PLAUSIBLE_API_KEY, used by your own export script or an http_json endpoint you control that returns the raw shape.",
        "csv_list_key": "days",
        "sample": _web_sample, "normalize": _web_norm},
    "sales": {
        "label": "Sales", "description": "Daily orders and revenue plus a recent-orders table.",
        "default_title": "Sales", "default_span": 1,
        "options": {"currency": _opt("currency", "USD", doc="3-letter currency code"), "days": _opt("int", 14, min=3, max=90, doc="days to show")},
        "raw_shape": {"currency": "USD", "days": [{"date": "YYYY-MM-DD", "orders": "int", "revenue": "number"}],
                      "recent": [{"date": "YYYY-MM-DD", "item": "string", "amount": "number"}]},
        "real_source": "Stripe (GET /v1/charges or balance transactions), Shopify, Gumroad, etc. Use a restricted READ-ONLY key in env STRIPE_API_KEY "
                       "inside your own export script that aggregates by day and writes the raw shape to a local file.",
        "csv_list_key": "days",
        "sample": _sales_sample, "normalize": _sales_norm},
    "ads": {
        "label": "Ads", "description": "Daily impressions, clicks, spend with CTR and CPC.",
        "default_title": "Ads", "default_span": 1,
        "options": {"platform": _opt("text", "Example Ads", max=40, doc="ad platform name"), "currency": _opt("currency", "USD", doc="3-letter currency code"),
                    "days": _opt("int", 7, min=3, max=90, doc="days to show")},
        "raw_shape": {"platform": "string", "currency": "USD", "days": [{"date": "YYYY-MM-DD", "impressions": "int", "clicks": "int", "spend": "number"}]},
        "real_source": "Google Ads / Meta Ads reporting APIs. Credentials (developer token, OAuth refresh token) live in env vars or a chmod 600 file on your "
                       "computer, read only by your export script, which writes the raw shape to a local file.",
        "csv_list_key": "days",
        "sample": _ads_sample, "normalize": _ads_norm},
    "bot_activity": {
        "label": "Bot activity", "description": "Which bots are active/idle/paused, what they last did, and how long ago.",
        "default_title": "Bot activity", "default_span": 1,
        "options": {"max_items": _opt("int", 12, min=1, max=100, doc="bots to list"),
                    "stale_after_hours": _opt("int", 24, min=1, max=24 * 365, doc="an active bot silent this long is flagged stale")},
        "raw_shape": {"bots": [{"name": "string", "status": "active|idle|paused", "last_update": "ISO datetime", "last_action": "string"}]},
        "real_source": "A status JSON your bots/cron jobs update (one entry per bot). No credentials needed: point source {mode:file} at it.",
        "csv_list_key": "bots",
        "sample": _bots_sample, "normalize": _bots_norm},
}
