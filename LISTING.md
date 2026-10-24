# Listing text

Copy for a marketplace page. Nothing here has been submitted anywhere.

**Name:** agent-dashboard (display name: Agent Dashboard)

**Short description (118 chars):** Local HTML dashboard your bot builds. Refresh is a plain script: no model calls, ~0.05 s CPU for 5 panels. No account.

**Long description:**

**Tell your bot "set up my dashboard" and get a private, drag-to-arrange dashboard on your own computer, with nothing to host and nothing to log in to.**

Refreshing your dashboard is a plain script: no model calls and no bot turns, about 0.05 s of CPU for a 5-panel build, and the idle server uses zero CPU. Your bot is only involved when you ask it to set up, add, move or open something, each one to three small tool calls (answers capped at 8 KB, never containing your data).

*Why you'll like it*

- **One sentence to set up.** Say "set up my dashboard": one tool call creates sample panels, builds the page and opens it. You edit no files.
- **Drag to reorder.** Press and hold a card's grip, then drag; or just tell your bot "move sales to the top".
- **Local only.** One HTML file on your own computer. The page loads nothing from the network, and the plugin makes network calls only to `http_json` sources you configure.
- **No bot usage on refresh for file and endpoint panels.** A cron line running `scripts/build.py` keeps them current. Panels fed through one of your bot's own connectors need a bot turn per refresh.
- **No account for the plugin.** No sign-up, no login, no hosting. (A data source you connect may have its own login; that is yours.) Keys stay on your computer and a leak guard fails the build if one would end up in the page.
- **Sample data first.** Every panel starts with clearly tagged fictional numbers; you connect real data one panel at a time.

*How it works*

1. **Install and say "set up my dashboard".** Your bot asks what you want to see (X stats, website visits, sales, ads, bot activity) and adds those panels with sample data through the plugin's tools.
2. **A plain script builds one HTML page** from the settings file (`out/dashboard.html` in your dashboard folder). Say "open my dashboard", or run `python3 scripts/build.py --open`, or put it on a cron schedule.
3. **Connect real data when you are ready:** an export file (`.json` or `.csv`), an https endpoint, or a connector your bot already has. See `docs/connect-a-source.md`.

*Limits, stated plainly:* setup puts about 2K tokens (estimated) of plugin text in front of your bot, once; panels fed through your bot's own connectors need a bot turn per refresh (use the file or endpoint route to avoid that); all numbers were measured on one VM and idle was measured for 45 s. Measurements and method: `docs/usage-audit.md`. Online or password-protected sharing is not part of v1.

**Tags:** dashboard, analytics, local-first, self-hosted, x, twitter-stats, sales, ads, website-traffic, bot-monitoring, no-account, mcp, skills

**Install line:** find `agent-dashboard` on cursor.directory (listing name `agent-dashboard`, display name Agent Dashboard) and install it from the plugin page. *Confirm at submission: the exact button text and any copy-paste install command on the listing page.* In the Cursor IDE you can also install a marketplace or directory plugin from Customize, then Install, choosing project or user scope. Then say: `set up my dashboard`

**Author:** `howdyollie`. **License:** MIT. **Repository / homepage:** https://github.com/howdyollie/agent-dashboard. **Contact:** https://github.com/howdyollie/agent-dashboard/issues

**Requirements:** Python 3.9 or newer (`python3`; on Windows set `"command": "python"` in `mcp.json`). A web browser. No network access needed unless you connect an `http_json` source.

**First prompts:** "set up my dashboard", "add my X stats", "open my dashboard", "move sales to the top"

**Screenshots** (all fictional "Sampleco" sample data):
- `docs/preview-desktop.png` desktop view
- `docs/preview-layout-menu.png` the Layout menu for reordering
- `docs/preview-mobile.png` phone view; `docs/preview-mobile-full.png` full-length phone view

**Privacy statement:** The plugin makes no network calls except to `http_json` sources you configure, and stores everything in a folder on your own computer.
