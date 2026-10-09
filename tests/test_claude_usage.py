"""Tests for the pure parsing/formatting logic in claude_usage.py.

The API fixtures below are synthetic (structure copied from real responses,
values invented) since the endpoint is undocumented — these tests pin down
what shape the parser expects, so a future breakage can be attributed to
either an endpoint change or a code change.
"""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_usage as cu

UTC = dt.timezone.utc

# Synthetic fixture: the current (2026) response shape, where the `limits`
# array is authoritative and flat fields like seven_day_opus may be null
# even when the corresponding window is live.
LIMITS_SHAPE = {
    "five_hour": {"utilization": 5.0, "resets_at": "2026-07-21T04:50:00+00:00"},
    "seven_day": {"utilization": 4.0, "resets_at": "2026-07-26T05:00:00+00:00"},
    "seven_day_opus": None,
    "extra_usage": {"is_enabled": True, "utilization": None},
    "limits": [
        {
            "kind": "session",
            "group": "session",
            "percent": 5,
            "resets_at": "2026-07-21T04:50:00+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 4,
            "resets_at": "2026-07-26T05:00:00+00:00",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 42,
            "resets_at": "2026-07-26T05:00:00+00:00",
            "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
            "is_active": False,
        },
        {
            "kind": "daily_widget",
            "group": "daily",
            "percent": 7,
            "resets_at": "2026-07-21T05:00:00+00:00",
            "scope": None,
            "is_active": True,
        },
    ],
}

# Synthetic fixture: older/fallback shape with no `limits` array.
FLAT_SHAPE = {
    "five_hour": {"utilization": 12.0, "resets_at": "2026-07-21T04:50:00+00:00"},
    "seven_day": {"utilization": 8.0, "resets_at": "2026-07-26T05:00:00+00:00"},
    "seven_day_opus": {"utilization": 63.0, "resets_at": "2026-07-26T05:00:00+00:00"},
    "some_new_window": {"utilization": 1.0, "resets_at": "2026-07-26T05:00:00+00:00"},
    "not_a_window": {"is_enabled": True},
}


class GetWindowsLimitsShape(unittest.TestCase):
    def test_limits_array_is_authoritative(self):
        windows = cu.get_windows(LIMITS_SHAPE)
        self.assertEqual(
            [w.label for w in windows],
            ["5h session", "week (all models)", "week (fable)", "daily widget"],
        )

    def test_scoped_window_read_from_limits_despite_null_flat_field(self):
        # seven_day_opus is null, but the Fable window must still appear.
        fable = [w for w in cu.get_windows(LIMITS_SHAPE) if w.short == "fable"]
        self.assertEqual(len(fable), 1)
        self.assertEqual(fable[0].percent, 42.0)

    def test_statusline_short_labels(self):
        windows = cu.get_windows(LIMITS_SHAPE)
        self.assertEqual([w.short for w in windows][:3], ["5h", "wk", "fable"])

    def test_unknown_kind_renders_generically(self):
        widget = [w for w in cu.get_windows(LIMITS_SHAPE) if w.key == "daily_widget"]
        self.assertEqual(len(widget), 1)
        self.assertEqual(widget[0].label, "daily widget")

    def test_entries_missing_fields_are_skipped(self):
        raw = {"limits": [{"kind": "session", "percent": 5}, {"kind": "x", "resets_at": "bogus", "percent": 1}]}
        self.assertEqual(cu.get_windows(raw), [])


class GetWindowsFlatFallback(unittest.TestCase):
    def test_flat_shape_parses_when_no_limits(self):
        windows = cu.get_windows(FLAT_SHAPE)
        self.assertEqual(
            [w.label for w in windows],
            ["5h session", "week (all models)", "week (fable)", "some new window"],
        )
        self.assertEqual(windows[2].percent, 63.0)

    def test_objects_without_utilization_ignored(self):
        keys = [w.key for w in cu.get_windows(FLAT_SHAPE)]
        self.assertNotIn("not_a_window", keys)

    def test_empty_input(self):
        self.assertEqual(cu.get_windows(None), [])
        self.assertEqual(cu.get_windows({}), [])


class Formatting(unittest.TestCase):
    def test_color_thresholds(self):
        self.assertEqual(cu.color_for(69.9), cu.GREEN)
        self.assertEqual(cu.color_for(70), cu.YELLOW)
        self.assertEqual(cu.color_for(89.9), cu.YELLOW)
        self.assertEqual(cu.color_for(90), cu.RED)

    def test_format_reset_relative(self):
        now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        rel, _ = cu.format_reset(now + dt.timedelta(hours=2, minutes=14), now)
        self.assertEqual(rel, "2h 14m")
        rel, _ = cu.format_reset(now + dt.timedelta(days=3, hours=2), now)
        self.assertEqual(rel, "3d 2h")
        rel, _ = cu.format_reset(now + dt.timedelta(minutes=9), now)
        self.assertEqual(rel, "9m")

    def test_format_reset_absolute_includes_weekday_when_days_away(self):
        now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        _, absolute = cu.format_reset(now + dt.timedelta(days=3), now)
        self.assertRegex(absolute, r"^[A-Z][a-z]{2} \d{2}:\d{2}$")

    def test_format_reset_past_clamps_to_zero(self):
        now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        rel, _ = cu.format_reset(now - dt.timedelta(hours=1), now)
        self.assertEqual(rel, "0m")

    def test_format_age(self):
        self.assertEqual(cu.format_age(None), "never")
        self.assertEqual(cu.format_age(dt.timedelta(seconds=42)), "42s ago")
        self.assertEqual(cu.format_age(dt.timedelta(minutes=7)), "7m ago")
        self.assertEqual(cu.format_age(dt.timedelta(hours=1, minutes=5)), "1h 5m ago")


class Staleness(unittest.TestCase):
    def test_fresh_cache_not_stale(self):
        cache = {"fetched_at": cu.utcnow().isoformat()}
        self.assertFalse(cu.is_stale(cache))

    def test_old_cache_is_stale(self):
        cache = {"fetched_at": (cu.utcnow() - dt.timedelta(minutes=20)).isoformat()}
        self.assertTrue(cu.is_stale(cache))

    def test_missing_cache_is_stale(self):
        self.assertTrue(cu.is_stale(None))
        self.assertTrue(cu.is_stale({}))


class CacheRobustness(unittest.TestCase):
    def test_malformed_fetched_at_is_stale_not_a_crash(self):
        self.assertIsNone(cu.cache_age({"fetched_at": "garbage"}))
        self.assertIsNone(cu.cache_age({"fetched_at": 123}))
        self.assertTrue(cu.is_stale({"fetched_at": "garbage"}))

    def test_naive_fetched_at_treated_as_utc(self):
        naive = (cu.utcnow() - dt.timedelta(minutes=1)).replace(tzinfo=None)
        age = cu.cache_age({"fetched_at": naive.isoformat()})
        self.assertLess(age, dt.timedelta(minutes=2))

    def test_load_cache_rejects_non_dict_and_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            for content in ("[1, 2]", "not json", ""):
                path.write_text(content)
                with mock.patch.object(cu, "CACHE_FILE", path):
                    self.assertIsNone(cu.load_cache())

    def test_save_cache_roundtrip_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cu, "CACHE_DIR", Path(d)), mock.patch.object(
                cu, "CACHE_FILE", Path(d) / "usage.json"
            ):
                cu.save_cache({"raw": None, "fetched_at": "x"})
                cu.save_cache({"raw": None, "fetched_at": "y"})
                self.assertEqual(cu.load_cache()["fetched_at"], "y")
            self.assertEqual([p.name for p in Path(d).iterdir()], ["usage.json"])

    def test_load_cache_missing_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(
            cu, "CACHE_FILE", Path(d) / "nope.json"
        ):
            self.assertIsNone(cu.load_cache())


class RetryAfter(unittest.TestCase):
    def test_parse_retry_after(self):
        self.assertEqual(cu.parse_retry_after({"Retry-After": "120"}), 120.0)
        self.assertIsNone(cu.parse_retry_after({}))
        self.assertIsNone(cu.parse_retry_after({"Retry-After": "Wed, 21 Oct"}))
        self.assertIsNone(cu.parse_retry_after({"Retry-After": "-5"}))

    def _cache_with_backoff(self, delta):
        until = (cu.utcnow() + delta).isoformat()
        old = (cu.utcnow() - dt.timedelta(minutes=30)).isoformat()
        return {"raw": {}, "fetched_at": old, "last_error": {"status": 429, "retry_after_until": until}}

    def test_backing_off(self):
        self.assertTrue(cu.backing_off(self._cache_with_backoff(dt.timedelta(minutes=10))))
        self.assertFalse(cu.backing_off(self._cache_with_backoff(-dt.timedelta(minutes=1))))
        self.assertFalse(cu.backing_off({"last_error": None}))

    def test_do_fetch_skips_network_while_backing_off(self):
        cache = self._cache_with_backoff(dt.timedelta(minutes=10))
        with mock.patch.object(cu, "load_cache", return_value=cache), mock.patch.object(
            cu, "get_access_token", side_effect=AssertionError("should not fetch")
        ):
            self.assertIs(cu.do_fetch(force=False), cache)

    def test_do_fetch_records_retry_after(self):
        saved = {}
        err = cu.FetchError(429, "HTTP 429", retry_after=600)
        with mock.patch.object(cu, "load_cache", return_value=None), mock.patch.object(
            cu, "get_access_token", return_value="t"
        ), mock.patch.object(cu, "call_usage_api", side_effect=err), mock.patch.object(
            cu, "save_cache", side_effect=saved.update
        ):
            cache = cu.do_fetch(force=False)
        self.assertTrue(cu.backing_off(cache))


class SessionSegments(unittest.TestCase):
    SESSION = {
        "model": {"id": "claude-fable-5", "display_name": "Fable 5"},
        "effort": {"level": "medium"},
        "context_window": {
            "total_input_tokens": 63_400,
            "total_output_tokens": 1_200,
            "context_window_size": 200_000,
            "used_percentage": 32.3,
        },
    }

    def test_model_effort_and_tokens(self):
        self.assertEqual(
            cu.session_segments(self.SESSION),
            ["Fable 5:medium", "65k tok (32% ctx)"],
        )

    def test_no_session(self):
        self.assertEqual(cu.session_segments(None), [])
        self.assertEqual(cu.session_segments({}), [])

    def test_model_without_effort(self):
        session = {"model": {"display_name": "Opus"}}
        self.assertEqual(cu.session_segments(session), ["Opus"])

    def test_tokens_without_percentage(self):
        session = {"context_window": {"total_input_tokens": 500, "total_output_tokens": 0}}
        self.assertEqual(cu.session_segments(session), ["500 tok"])

    def test_null_current_usage_fields_ignored(self):
        session = {
            "model": {"display_name": "Fable 5"},
            "context_window": {
                "total_input_tokens": None,
                "total_output_tokens": None,
                "used_percentage": None,
            },
        }
        self.assertEqual(cu.session_segments(session), ["Fable 5"])

    def test_format_tokens(self):
        self.assertEqual(cu.format_tokens(999), "999")
        self.assertEqual(cu.format_tokens(64_600), "65k")
        self.assertEqual(cu.format_tokens(1_234_000), "1.2M")


if __name__ == "__main__":
    unittest.main()
