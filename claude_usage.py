#!/usr/bin/env python3
"""Claude usage monitor.

Fetches Anthropic's undocumented OAuth usage-check-in endpoint, caches the
result locally, and renders it through three Frontends: the Terminal
Command (this script with no flags, or --refresh), the Web Page
(--html / dashboard/index.html), and the Statusline (--statusline).

Only --fetch and --refresh ever touch the network; everything else reads
the Cache. See CONTEXT.md for the vocabulary used throughout this file
(Usage Window, Reset Time, Cache, Staleness, Threshold Bands, ...).

Stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_URL = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.0.0"
ANTHROPIC_BETA = "oauth-2025-04-20"
KEYCHAIN_SERVICE = "Claude Code-credentials"

CACHE_DIR = Path.home() / ".cache" / "claude-usage"
CACHE_FILE = CACHE_DIR / "usage.json"

REPO_ROOT = Path(__file__).resolve().parent
DASHBOARD_PATH = REPO_ROOT / "dashboard" / "index.html"

MIN_FETCH_INTERVAL = dt.timedelta(minutes=5)
STALE_AFTER = dt.timedelta(minutes=12)

# Known `limits[].kind` values -> (friendly label, Statusline short label).
# `weekly_scoped` entries are labeled from their scope's model display name.
# Anything else renders generically from its kind (see get_windows).
LIMIT_KINDS = {
    "session": ("5h session", "5h"),
    "weekly_all": ("week (all models)", "wk"),
}

# Fallback shape only: known flat top-level field names -> labels, in display
# order, for responses without a `limits` array.
KNOWN_WINDOWS = [
    ("five_hour", "5h session"),
    ("seven_day", "week (all models)"),
    ("seven_day_opus", "week (fable)"),
]

STATUSLINE_LABELS = {
    "five_hour": "5h",
    "seven_day": "wk",
    "seven_day_opus": "fable",
}

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


class TokenError(Exception):
    """Raised when the OAuth token can't be read from the Keychain."""


class FetchError(Exception):
    """Raised when the usage endpoint can't be reached or returns an error.

    `status` is an HTTP status code when known, or a short string like
    "network_error" / "token_error" otherwise.
    """

    def __init__(self, status: Any, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------
# Token + fetch
# --------------------------------------------------------------------------


def get_access_token() -> str:
    """Read (read-only) the OAuth access token from the macOS Keychain."""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:  # e.g. FileNotFoundError, timeout
        raise TokenError(f"could not run `security`: {e}") from e

    if result.returncode != 0:
        raise TokenError(
            f"Keychain read failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
        token = data["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise TokenError(f"unexpected Keychain credential shape: {e}") from e

    if not token:
        raise TokenError("empty access token in Keychain credential")
    return token


def call_usage_api(token: str) -> dict:
    """One authoritative Fetch of all Usage Windows from Anthropic."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": ANTHROPIC_BETA,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise FetchError(e.code, f"HTTP {e.code}: {body[:200]}") from e
    except urllib.error.URLError as e:
        raise FetchError("network_error", str(e.reason)) from e
    except Exception as e:
        raise FetchError("error", str(e)) from e


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_cache() -> dict | None:
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    tmp.replace(CACHE_FILE)


def cache_age(cache: dict | None) -> dt.timedelta | None:
    if not cache or not cache.get("fetched_at"):
        return None
    fetched_at = dt.datetime.fromisoformat(cache["fetched_at"])
    return utcnow() - fetched_at


def is_stale(cache: dict | None) -> bool:
    age = cache_age(cache)
    return age is None or age > STALE_AFTER


def do_fetch(force: bool) -> dict:
    """Fetch fresh usage data if allowed, updating and returning the Cache.

    On failure, the last good Cache (raw + fetched_at) is preserved and a
    `last_error` is recorded so Frontends can surface it.
    """
    cache = load_cache() or {"raw": None, "fetched_at": None, "last_error": None}

    if not force:
        age = cache_age(cache)
        if age is not None and age < MIN_FETCH_INTERVAL:
            return cache

    try:
        token = get_access_token()
        raw = call_usage_api(token)
    except TokenError as e:
        cache["last_error"] = {
            "status": "token_error",
            "message": str(e),
            "at": utcnow().isoformat(),
        }
        save_cache(cache)
        return cache
    except FetchError as e:
        cache["last_error"] = {
            "status": e.status,
            "message": e.message,
            "at": utcnow().isoformat(),
        }
        save_cache(cache)
        return cache

    cache["raw"] = raw
    cache["fetched_at"] = utcnow().isoformat()
    cache["last_error"] = None
    save_cache(cache)
    return cache


# --------------------------------------------------------------------------
# Parsing (generic + defensive)
# --------------------------------------------------------------------------


class Window:
    def __init__(self, key: str, label: str, percent: float, resets_at: dt.datetime, short: str):
        self.key = key
        self.label = label
        self.percent = percent
        self.resets_at = resets_at
        self.short = short


def prettify_key(key: str) -> str:
    return key.replace("_", " ")


def get_windows(raw: dict | None) -> list[Window]:
    """Render ALL Usage Windows found in the response.

    Primary source is the `limits` array — the authoritative list of active
    windows, including model-scoped ones (e.g. the Fable window) that are
    null in the flat top-level fields. Unknown kinds render generically, so
    windows Anthropic adds later just appear.

    Fallback for responses without `limits`: any top-level object with a
    numeric `utilization` and a non-null `resets_at` counts as a window.
    """
    if not raw:
        return []

    windows: list[Window] = []
    limits = raw.get("limits")
    if isinstance(limits, list):
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            percent = entry.get("percent")
            resets_at_raw = entry.get("resets_at")
            if percent is None or resets_at_raw is None:
                continue
            try:
                resets_at = dt.datetime.fromisoformat(resets_at_raw)
            except (TypeError, ValueError):
                continue
            kind = entry.get("kind") or "unknown"
            scope_name = None
            scope = entry.get("scope")
            if isinstance(scope, dict) and isinstance(scope.get("model"), dict):
                scope_name = scope["model"].get("display_name")
            if kind in LIMIT_KINDS:
                label, short = LIMIT_KINDS[kind]
            elif kind == "weekly_scoped" and scope_name:
                label = f"week ({scope_name.lower()})"
                short = scope_name.lower()
            else:
                label = prettify_key(kind)
                if scope_name:
                    label += f" ({scope_name.lower()})"
                short = label
            key = f"{kind}:{scope_name}" if scope_name else kind
            windows.append(Window(key, label, float(percent), resets_at, short))
    if windows:
        return windows

    known_order = [k for k, _ in KNOWN_WINDOWS]
    known_labels = dict(KNOWN_WINDOWS)

    found: dict[str, Window] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        utilization = value.get("utilization")
        resets_at_raw = value.get("resets_at")
        if utilization is None or resets_at_raw is None:
            continue
        try:
            resets_at = dt.datetime.fromisoformat(resets_at_raw)
        except (TypeError, ValueError):
            continue
        label = known_labels.get(key, prettify_key(key))
        short = STATUSLINE_LABELS.get(key, label)
        found[key] = Window(key, label, float(utilization), resets_at, short)

    ordered: list[Window] = []
    for key in known_order:
        if key in found:
            ordered.append(found.pop(key))
    ordered.extend(found[k] for k in sorted(found.keys()))
    return ordered


# --------------------------------------------------------------------------
# Formatting helpers shared by Terminal Command / Web Page / Statusline
# --------------------------------------------------------------------------


def color_for(percent: float) -> str:
    if percent >= 90:
        return RED
    if percent >= 70:
        return YELLOW
    return GREEN


def format_reset(resets_at: dt.datetime, now: dt.datetime | None = None) -> tuple[str, str]:
    """Return (relative, absolute) Reset Time strings, in local time."""
    now = now or utcnow()
    delta = resets_at - now
    total_seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    if days > 0:
        relative = f"{days}d {hours}h"
    elif hours > 0:
        relative = f"{hours}h {minutes}m"
    else:
        relative = f"{minutes}m"

    local_dt = resets_at.astimezone()
    now_local = now.astimezone()
    if days > 0 or local_dt.date() != now_local.date():
        absolute = local_dt.strftime("%a %H:%M")
    else:
        absolute = local_dt.strftime("%H:%M")

    return relative, absolute


def format_age(age: dt.timedelta | None) -> str:
    if age is None:
        return "never"
    seconds = max(0, int(age.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m ago"


# --------------------------------------------------------------------------
# Terminal Command
# --------------------------------------------------------------------------


def render_terminal(cache: dict | None) -> None:
    use_color = sys.stdout.isatty()
    windows = get_windows(cache.get("raw") if cache else None)

    if not windows:
        print("No cached usage data yet. Run `claude-usage --refresh` to fetch.")
        return

    label_width = max(len(w.label) for w in windows)
    bar_width = 20

    for w in windows:
        percent = max(0.0, min(100.0, w.percent))
        filled = round(percent / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        relative, absolute = format_reset(w.resets_at)

        if use_color:
            color = color_for(percent)
            bar = f"{color}{bar}{RESET}"
            pct = f"{color}{percent:3.0f}%{RESET}"
        else:
            pct = f"{percent:3.0f}%"

        print(f"{w.label:<{label_width}}  {bar}  {pct}  resets in {relative} ({absolute})")

    age = cache_age(cache)
    footer = f"updated {format_age(age)}"
    if is_stale(cache):
        footer += "  [stale]"
    last_error = (cache or {}).get("last_error")
    if last_error:
        if last_error.get("status") == 401:
            footer += "  -- auth expired, open Claude Code to re-auth"
        else:
            footer += f"  -- last fetch failed ({last_error.get('status')})"

    if use_color:
        print(f"{DIM}{footer}{RESET}")
    else:
        print(footer)


# --------------------------------------------------------------------------
# Statusline
# --------------------------------------------------------------------------


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def session_segments(session: dict | None, use_color: bool = False) -> list[str]:
    """Extra Statusline segments from the session JSON Claude Code pipes in:
    model:effort and current context-window token usage."""
    if not session:
        return []
    segments = []

    model = (session.get("model") or {}).get("display_name")
    if model:
        effort = (session.get("effort") or {}).get("level")
        segments.append(f"{model}:{effort}" if effort else model)

    ctx = session.get("context_window") or {}
    tokens = (ctx.get("total_input_tokens") or 0) + (ctx.get("total_output_tokens") or 0)
    if tokens:
        text = f"{format_tokens(tokens)} tok"
        percent = ctx.get("used_percentage")
        if isinstance(percent, (int, float)):
            if use_color:
                text = f"{color_for(percent)}{text}{RESET}"
            text += f" ({percent:.0f}% ctx)"
        segments.append(text)

    return segments


def render_statusline(cache: dict | None, session: dict | None = None) -> None:
    use_color = sys.stdout.isatty()
    raw = (cache or {}).get("raw")

    segments = []
    for w in get_windows(raw):
        percent = max(0.0, min(100.0, w.percent))
        pct_text = f"{percent:.0f}%"
        if use_color:
            color = color_for(percent)
            pct_text = f"{color}{pct_text}{RESET}"
        segment = f"{w.short} {pct_text}"
        if percent >= 80:
            _, absolute = format_reset(w.resets_at)
            segment += f" (resets {absolute})"
        segments.append(segment)

    if not segments:
        segments.append("no usage data")

    segments.extend(session_segments(session, use_color))

    line = "⚡ " + " · ".join(segments)

    age = cache_age(cache)
    if is_stale(cache) and age is not None:
        stale_minutes = int(age.total_seconds() // 60)
        line += f" (stale {stale_minutes}m)"

    print(line)


# --------------------------------------------------------------------------
# Web Page
# --------------------------------------------------------------------------


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def generate_html(cache: dict | None) -> None:
    windows = get_windows(cache.get("raw") if cache else None)
    fetched_at = (cache or {}).get("fetched_at")
    last_error = (cache or {}).get("last_error")

    rows = []
    for w in windows:
        percent = max(0.0, min(100.0, w.percent))
        if percent >= 90:
            css_class = "red"
        elif percent >= 70:
            css_class = "yellow"
        else:
            css_class = "green"
        relative, absolute = format_reset(w.resets_at)
        rows.append(
            f"""
      <div class="window">
        <div class="window-header">
          <span class="label">{html_escape(w.label)}</span>
          <span class="percent {css_class}">{percent:.0f}%</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill {css_class}" style="width: {percent:.1f}%"></div>
        </div>
        <div class="reset">resets in {html_escape(relative)} ({html_escape(absolute)})</div>
      </div>"""
        )

    if not rows:
        rows.append('<p class="empty">No cached usage data yet.</p>')

    error_html = ""
    if last_error:
        if last_error.get("status") == 401:
            error_html = '<p class="error">Auth expired &mdash; open Claude Code to re-auth.</p>'
        else:
            error_html = (
                f'<p class="error">Last fetch failed '
                f'({html_escape(str(last_error.get("status")))}).</p>'
            )

    fetched_at_js = json.dumps(fetched_at)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<meta name="theme-color" content="#1a1a2e">
<link rel="icon" type="image/svg+xml" href="icon.svg">
<title>Claude Usage</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff;
    --fg: #1a1a1a;
    --muted: #6b7280;
    --card-bg: #f5f5f7;
    --track: #e5e7eb;
    --green: #22c55e;
    --yellow: #eab308;
    --red: #ef4444;
    --error: #ef4444;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #111114;
      --fg: #f2f2f2;
      --muted: #9ca3af;
      --card-bg: #1c1c22;
      --track: #33333a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem 1.25rem;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex;
    justify-content: center;
  }}
  main {{
    width: 100%;
    max-width: 560px;
  }}
  h1 {{
    font-size: 1.1rem;
    font-weight: 600;
    margin: 0 0 1.25rem;
    letter-spacing: 0.01em;
  }}
  .window {{
    background: var(--card-bg);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 0.75rem;
  }}
  .window-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.5rem;
  }}
  .label {{
    font-weight: 500;
  }}
  .percent {{
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}
  .percent.green {{ color: var(--green); }}
  .percent.yellow {{ color: var(--yellow); }}
  .percent.red {{ color: var(--red); }}
  .bar-track {{
    background: var(--track);
    border-radius: 999px;
    height: 10px;
    overflow: hidden;
  }}
  .bar-fill {{
    height: 100%;
    border-radius: 999px;
  }}
  .bar-fill.green {{ background: var(--green); }}
  .bar-fill.yellow {{ background: var(--yellow); }}
  .bar-fill.red {{ background: var(--red); }}
  .reset {{
    margin-top: 0.4rem;
    font-size: 0.82rem;
    color: var(--muted);
  }}
  .empty {{ color: var(--muted); }}
  .error {{
    color: var(--error);
    font-size: 0.85rem;
  }}
  footer {{
    margin-top: 1.5rem;
    font-size: 0.8rem;
    color: var(--muted);
  }}
</style>
</head>
<body>
<main>
  <h1>Claude Usage</h1>
  {"".join(rows)}
  {error_html}
  <footer id="age">updated &mdash;</footer>
</main>
<script>
  const fetchedAt = {fetched_at_js};
  const staleAfterMs = {int(STALE_AFTER.total_seconds() * 1000)};
  function render() {{
    const el = document.getElementById("age");
    if (!fetchedAt) {{
      el.textContent = "no data fetched yet";
      return;
    }}
    const fetchedMs = new Date(fetchedAt).getTime();
    const ageMs = Date.now() - fetchedMs;
    const seconds = Math.max(0, Math.floor(ageMs / 1000));
    let text;
    if (seconds < 60) {{
      text = `updated ${{seconds}}s ago`;
    }} else if (seconds < 3600) {{
      text = `updated ${{Math.floor(seconds / 60)}}m ago`;
    }} else {{
      text = `updated ${{Math.floor(seconds / 3600)}}h ${{Math.floor((seconds % 3600) / 60)}}m ago`;
    }}
    if (ageMs > staleAfterMs) {{
      text += " (stale)";
    }}
    el.textContent = text;
  }}
  render();
  setInterval(render, 1000);
</script>
</body>
</html>
"""

    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def read_session_stdin() -> dict | None:
    """Statusline callers pass session JSON (model, effort, context window,
    ...) on stdin. Parse it; on a tty or bad input, return None."""
    try:
        if sys.stdin.isatty():
            return None
        data = json.loads(sys.stdin.read())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude usage monitor")
    parser.add_argument("--refresh", action="store_true", help="Force a fetch, then print")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch if cache is stale, regenerate dashboard, print nothing",
    )
    parser.add_argument(
        "--statusline",
        action="store_true",
        help="Print one compact line for the Claude Code statusline (cache-only)",
    )
    parser.add_argument("--html", action="store_true", help="Regenerate the dashboard only")
    args = parser.parse_args()

    if args.statusline:
        session = read_session_stdin()
        render_statusline(load_cache(), session)
        return 0

    if args.fetch:
        cache = do_fetch(force=False)
        generate_html(cache)
        return 0

    if args.refresh:
        cache = do_fetch(force=True)
        render_terminal(cache)
        return 0

    if args.html:
        generate_html(load_cache())
        return 0

    render_terminal(load_cache())
    return 0


if __name__ == "__main__":
    sys.exit(main())
