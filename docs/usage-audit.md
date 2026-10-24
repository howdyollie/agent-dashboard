# Usage audit: what this plugin costs to run

Question: does a dashboard built with this plugin burn usage (bot tokens, model calls, CPU, network)?
Short answer, with numbers below: **refreshing the page uses no model calls and no bot turns, and about 0.05 to 0.16 s of CPU. The bot is only involved when you ask it to do something (setup, add, move, open), and each of those is one to three small tool calls.** The numbers can be re-run with `python3 tests/measure_usage.py` (details at the end).

All numbers come from one Linux VM (8 cores, Python 3.13), median of 7 runs unless stated. Token counts are estimates (bytes / 4). They are not billing figures.

## 1. Refreshing the page: no model, no bot

| Check | Result |
|---|---|
| Model or LLM client code in `dashlib/`, `server/`, `scripts/` | None. Word-boundary scan for provider names, "completion", "gpt", "api_key" etc. finds only the secret-detection code (`api_key` as a name pattern) and the phrase "no LLM" in two docstrings and one tool description. |
| Code that can open a network connection | Only `dashlib/sources.py` (`urllib.request`), used only for panels you set to `http_json`. Nothing else imports a network module. |
| The page itself | No `fetch`, no XHR, no WebSocket, no meta refresh, no `setInterval`, no external script/style/image. |
| Build with the network switched off | `scripts/build.py` and the MCP `rebuild` and `open_dashboard` (no browser) all succeed inside a network namespace (see method). A probe in the same namespace confirmed connect and DNS are blocked. Also passes with Python sockets patched to raise. |
| Cron line in the docs | Runs `python3 .../scripts/build.py` only. No bot, no model. |

Method: `unshare -rn` (new user + network namespace; no interface except a down loopback). Inside it the script first tries `socket.connect` and a DNS lookup (both fail), then runs the build as a normal command and as the real MCP server over stdio. Run it with `python3 tests/measure_usage.py network`.

### Runtime, CPU and memory of a build (median of 7)

| Scenario | Panels | Input read | Wall | CPU | Peak memory | Page / data.json |
|---|---|---|---|---|---|---|
| Python start-up alone (the floor) | 0 | 0 | 0.008 s | 0.008 s | 17.1 MB | n/a |
| Sample data (what setup creates) | 5 | 0 | 0.047 s | 0.044 s | 21.9 MB | 37 KB / 9 KB |
| Realistic files, 500 rows per list | 5 | 0.46 MB | 0.066 s | 0.064 s | 22.8 MB | 43 KB / 16 KB |
| 5 files padded to the 2 MB cap | 5 | 10 MB | 0.079 s | 0.076 s | 30.4 MB | 43 KB / 16 KB |
| Maximum panels, 2 MB files | 20 | 10 MB | 0.164 s | 0.161 s | 30.4 MB | 79 KB / 62 KB |

Builds that call a live endpoint also wait for the network (up to the panel's time limit, see caps); that waiting is not CPU.

## 2. The MCP server when nobody calls it

The server is a plain stdin read loop: no threads, timers, signal handlers, sleep loops, file watchers or auto-refresh. A test (`tests/test_caps.py`) greps every shipped Python file for those and fails if any appear.

Measured with `/proc` after start-up and `initialize`, then 45 s with no calls:

| Metric | Result |
|---|---|
| Process state while idle | `S` (sleeping, blocked on stdin) |
| Threads | 1 at start, 1 at end |
| CPU used during the 45 s | 0.00 s (0.03 s total, all of it start-up) |
| Voluntary / involuntary context switches | 2 / 0 |
| Output while idle | none |
| Child processes | 0 |

Timers that exist in the page (the local UI only, nothing is fetched): `setTimeout` for the ~350 ms press-and-hold before a card can be dragged, and `requestAnimationFrame` while a card is being dragged. Nothing runs when the page is just sitting open.

## 3. Where the bot is involved

The bot is involved when you ask it to do something. It is not involved in refreshing, unless a panel uses the connector route (see limits).

Scripted fresh-user session against the real MCP server (bytes on the wire for the tool calls; a "call" is one tool call):

| Action | Calls | Request bytes | Response bytes | Before trimming (response) |
|---|---|---|---|---|
| Handshake (`initialize` + `tools/list`), once per session | 2 | 226 | 5,435 | 6,470 |
| First-time setup, 5 sample panels, built and opened | 1 (`setup`) | 216 | 1,270 | 1,783 |
| Add one panel and show it | 2 | 254 | 1,027 | 1,346 |
| Reorder and show | 2 | 308 | 968 | 1,271 |
| "Open my dashboard" | 1 | 121 | 776 | 1,032 |
| Remove a panel (including a lookup) and show | 3 | 345 | 2,063 | 2,950 |
| Look up the full panel library (optional) | 1 | 111 | 5,391 | 7,391 |

What the bot loads into its context, before and after this audit's trimming:

| Item | Before | After |
|---|---|---|
| `tools/list` payload (10 tools) | 5,956 B (~1,489 tokens) | 4,917 B (~1,229 tokens) |
| Three skill files, total | 12,178 B, 117 lines (~3,043 tokens) | 6,276 B, 60 lines (~1,567 tokens) |
| Skill descriptions (always loaded) | 1,110 B | 673 B |
| Largest skill when triggered | 6,035 B | 2,923 B |

A first-time setup therefore puts roughly 8.7 KB (about 2.2K tokens by the bytes/4 estimate) of plugin text and tool traffic in front of the bot: the tool list, the skill descriptions, the setup skill, and one tool call with its answer. Before trimming it was about 12.2 KB (about 3.0K tokens). Later requests such as "open my dashboard" add under 1 KB of tool traffic each.

What is not measured: the bot's own reasoning, its chat replies, how the host counts tokens, or any other tools the host loads. Those depend on the host and the model, and are not zero.

## 4. Worst cases and the caps that bound them

Every cap has a test in `tests/test_caps.py`.

| Risk | Cap or default | Behaviour |
|---|---|---|
| Huge source file | 2 MB per file | That panel shows an error card with a plain "Fix:"; other panels still build. |
| Huge or endless endpoint response | 2 MB per response, read in 64 KB chunks | Error card. |
| Slow endpoint or slow-drip response | Time limit per request: default 10 s, range 1 to 30 s, counted as a total, not per read | Error card. A server sending one byte every 0.25 s is cut at the limit. |
| Many slow panels | Whole-build budget 60 s (`DASHBOARD_BUILD_TIMEOUT`, clamped 5 to 600) | Remaining panels get an error card. |
| Retry loops | No automatic retries anywhere | Each URL is requested exactly once per build (tested, including a 500 response). The skills say not to repeat a failed call without changing something. |
| Too many panels | 20 per dashboard | Refused with a fix. |
| Huge config | `config.json` 256 KB, `secrets.env` 64 KB | Config refused with a fix; oversized key file is ignored with a warning. |
| Huge lists | 500 items per list; days at most 90, posts 50, items 100, recent 20 | Over 500 items gives an error card; otherwise the page only gets the capped rows. |
| Deeply nested or hostile JSON | JSON depth errors are caught | Error card, no crash. |
| Huge build output | 8 MB for page + data.json | Build refused with a fix. In the tests, 20 panels of maximum data produce a 79 KB page. |
| Huge answers to the bot | 8 KB per tool answer; panel error text cut to 420 characters (140 when more than five panels fail); compact JSON | Tests with 20 panels, 2 MB source files and failing sources assert every answer is at most 8 KB and never contains source data or page HTML. Tools return summaries and paths only. |

## 5. Honest limitations

- **Setup is not free for the bot.** Setup costs the plugin text above plus whatever the bot spends talking to you. That is a small, one-time amount, but not zero.
- **The connector route needs a bot turn per refresh.** If a panel's data comes through one of the bot's own connectors, the bot has to fetch it each time, and that costs whatever that connector call costs. For refreshes that never involve a bot, use the **file** route (a `.json` or `.csv` in `data/`, refreshed by your own script) or the **endpoint** route (`http_json`). Both refresh with `scripts/build.py` alone, which you can put on a schedule.
- **The endpoint route uses the network** while building, bounded by the limits above, and your own service's usage is yours.
- Opening the page uses your browser like any local file; browser cost is not measured here.
- Numbers are from one machine and will differ on yours. Token figures are estimates (bytes / 4), not billing data.
- Idle was measured for 45 s, and the code review shows nothing that could wake the server; this is not a measurement of days.

## Reproducing

```
python3 tests/measure_usage.py --runs 7 --idle 45 --json usage.json     # all sections
python3 tests/measure_usage.py static network perf idle session sizes      # pick sections
python3 tests/test_all.py                                                   # includes the cap tests
```

`network` needs unprivileged user namespaces (`unshare -rn`); if unavailable the script says so and uses the socket-patch check only.
