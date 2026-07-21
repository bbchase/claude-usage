# ADR 0001: Use the undocumented OAuth usage endpoint

## Status

Accepted

## Context

We want to monitor how close a Claude subscription is to hitting its rate
limits: the Session Window, the Weekly Window, and the Fable Window (see
CONTEXT.md for definitions). Anthropic does not publish a documented API for
reading a subscription's current Usage Windows.

Two approaches were considered:

1. **Estimate usage locally** by parsing Claude Code's local JSONL session
   logs (token counts per model, per session) and reconstructing an
   approximation of the 5-hour and 7-day windows.
2. **Call the same endpoint Claude Desktop's usage indicator calls**:
   `GET https://api.anthropic.com/api/oauth/usage`, undocumented but
   observable, authenticated with the same OAuth access token Claude Code
   already stores in the macOS Keychain.

Estimating from local logs would require independently modeling Anthropic's
rate-limit accounting (window boundaries, model grouping, discounting rules)
with no ground truth to validate against, and would drift silently as those
rules change. It also can't see usage from other clients/devices on the same
account. The real endpoint returns Anthropic's own authoritative numbers,
including `resets_at` timestamps we would otherwise have to infer.

Constraints on using it directly:

- No official support or stability guarantee; the shape is
  reverse-engineered, not documented.
- Rate limited: safe at roughly one request per 3 minutes; we poll at most
  once per 5 minutes to stay well clear of that.
- Requires a bearer token. We read the existing Claude Code OAuth access
  token read-only from the Keychain entry `Claude Code-credentials`; we never
  write to the Keychain and never attempt to refresh tokens ourselves.

## Decision

Build the monitor directly on `GET /api/oauth/usage`, fetched at most every
5 minutes, with the raw response and fetch timestamp written to a local file
Cache (`~/.cache/claude-usage/usage.json`). All three Frontends (Terminal
Command, Web Page, Statusline) read only the Cache; nothing but the fetch
path ever calls the network.

Parsing is defensive and generic by design: any top-level field of the
response shaped like a Usage Window (an object with a numeric `utilization`
and a non-null `resets_at`) is rendered, whether or not we recognize its key.
Known keys (`five_hour`, `seven_day`, `seven_day_opus`) get friendly labels;
anything else — including fields Anthropic adds later — falls back to a
generic label derived from its field name.

## Consequences

- The endpoint can change shape or disappear without notice; this is an
  accepted risk for an unpublished API.
- Mitigations:
  - Defensive, generic parsing (above) absorbs new/renamed Usage Windows
    without a code change, and simply drops fields that no longer look like
    windows instead of crashing.
  - The Cache always keeps the last good response. On a 401 the monitor
    shows a clear "open Claude Code to re-auth" hint rather than treating it
    as a hard failure; on other errors (429, network failure, endpoint
    removed) it shows a failed-fetch note but keeps displaying the last known
    good numbers with their Staleness age.
  - Polling is capped at once per 5 minutes regardless of how many Frontends
    are consulted, so even three Frontends checked constantly cannot exceed
    Anthropic's rate limit.
- If Anthropic ever publishes an official, documented endpoint for
  subscription usage, this ADR should be revisited and the monitor migrated
  to it.
