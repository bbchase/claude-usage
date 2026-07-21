# Context: Claude Usage Monitor

Glossary of terms for this project. Definitions only — no implementation details.

## Terms

**Usage Window** — A rate-limit bucket tracked by Anthropic for a subscription account, with a percentage used and a reset time. Known windows: the Session Window, the Weekly Window, and the Fable Window. Unknown windows may appear in API responses and are still Usage Windows.

**Session Window** — The 5-hour rolling Usage Window covering all activity in the current session block.

**Weekly Window** — The weekly Usage Window covering all models.

**Fable Window** — The weekly Usage Window specific to Fable/Opus-class models.

**Reset Time** — The instant a Usage Window's percentage returns to zero. Displayed both relatively ("in 2h 14m") and absolutely ("14:00", with weekday for weekly windows).

**Fetch** — One authoritative retrieval of all Usage Windows from Anthropic. Fetches are rate-limited by Anthropic, so they happen at most every ~5 minutes.

**Cache** — The locally stored result of the most recent successful Fetch, including when it was fetched. All Frontends read only the Cache.

**Staleness** — The age of the Cache. Data older than the normal refresh cycle is Stale and must be labeled as such, not hidden.

**Frontend** — A way of viewing the Cache. There are three: the Terminal Command, the Web Page, and the Statusline.

**Terminal Command** — The `claude-usage` command; prints all Usage Windows with bars and Reset Times.

**Web Page** — A static, self-reloading HTML dashboard pinned in a browser tab.

**Statusline** — The compact single-line view inside Claude Code showing percentages for the three known windows.

**Threshold Bands** — The color coding applied to a Usage Window's percentage: green below 70%, yellow 70–89%, red at 90% and above. At 80%+ the Statusline also shows that window's Reset Time.
