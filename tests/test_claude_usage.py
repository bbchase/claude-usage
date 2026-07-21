"""Tests for the pure parsing/formatting logic in claude_usage.py.

The API fixtures below are synthetic (structure copied from real responses,
values invented) since the endpoint is undocumented — these tests pin down
what shape the parser expects, so a future breakage can be attributed to
either an endpoint change or a code change.
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
