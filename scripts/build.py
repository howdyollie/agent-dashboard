#!/usr/bin/env python3
"""Rebuild the dashboard page. Plain script: no LLM, safe for cron.

  python3 scripts/build.py                 # uses $DASHBOARD_HOME (default ~/.agent-dashboard)
  python3 scripts/build.py --home DIR      # explicit dashboard folder (holds config.json, writes out/)
  python3 scripts/build.py --open          # rebuild, then open the page in the default browser
Exit codes: 0 ok, 2 config error, 3 secret-leak guard tripped (nothing written)."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dashlib.build import LeakError, build  # noqa: E402
from dashlib.config import ConfigError  # noqa: E402
from dashlib.opener import open_dashboard  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", help="dashboard folder (default $DASHBOARD_HOME or ~/.agent-dashboard)")
    ap.add_argument("--open", action="store_true", help="after building, open the page in the default browser")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args()
    try:
        r = open_dashboard(a.home) if a.open else build(a.home)
    except ConfigError as e:
        print("config error:", e, file=sys.stderr)
        return 2
    except LeakError as e:
        print("LEAK GUARD:", e, file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        bad = [p for p in r["panels"] if p["status"] != "ok"]
        print("wrote %s (%d panels, %d with errors)" % (r["html"], len(r["panels"]), len(bad)))
        if a.open:
            print("opened in browser" if r["opened"] else "could not launch a browser; open the file manually")
        for p in bad:
            print("  panel %s: %s" % (p["id"], p["error"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
