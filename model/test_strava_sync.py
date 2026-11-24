import unittest

from pacer import parse_time
from strava_sync import RateLimited, sync

PROFILE = {"distance": [0, 300], "altitude": [10, 30], "latlng": [[32.0, 118.8], [32.0, 118.9]]}


def fake_strava(limit_after=None):
    """Two saved routes sharing segment 7; segment 8 has a 45 s KOM. Records every call."""
    calls = []
    data = {
        "/athletes/1/routes": {1: [{"id": 11, "id_str": "11"}, {"id": 12, "id_str": "12"}], 2: []},
        "/routes/11": {"name": "Purple Mt loop", "segments": [{"id": 7}, {"id": 8}]},
        "/routes/12": {"name": "Wall repeats", "segments": [{"id": 7}]},
        "/segments/7": {"name": "Wall", "distance": 559, "average_grade": 4.9, "xoms": {"kom": "1:21", "qom": "1:40"},
                        "athlete_segment_stats": {"pr_elapsed_time": 120, "pr_date": "2026-04-11"}},
        "/segments/8": {"name": "Kicker", "distance": 300, "average_grade": 6.0, "xoms": {"kom": "45s"}},
        "/segments/7/streams": {k: {"data": v} for k, v in PROFILE.items()},
        "/segments/8/streams": {k: {"data": v} for k, v in PROFILE.items()},
    }

    def get(path, **params):
        calls.append(path)
        if limit_after is not None and len(calls) > limit_after:
            raise RateLimited
        reply = data[path]
        return reply[params["page"]] if "page" in params else reply
    return get, calls


class SyncTest(unittest.TestCase):
    def test_collects_route_segments_with_kom_and_pr(self):
        get, calls = fake_strava()
        segments, complete = sync(get, 1, known={})
        self.assertTrue(complete)
        self.assertEqual(segments["7"]["routes"], ["Purple Mt loop", "Wall repeats"])
        self.assertEqual((segments["7"]["kom_s"], segments["7"]["qom_s"], segments["7"]["pr_s"]), (81, 100, 120))
        self.assertEqual((segments["8"]["kom_s"], segments["8"]["pr_s"]), (45, None))
        self.assertEqual(segments["8"]["profile"], PROFILE)
        self.assertEqual(calls.count("/segments/7"), 1)  # shared segment fetched once

    def test_known_profiles_are_not_downloaded_again(self):
        get, calls = fake_strava()
        sync(get, 1, known={"7": {"profile": PROFILE}})
        self.assertNotIn("/segments/7/streams", calls)
        self.assertIn("/segments/8/streams", calls)

    def test_rate_limit_keeps_last_runs_records(self):
        get, _ = fake_strava(limit_after=4)  # dies before segment 8
        old = {"8": {"name": "Kicker (last run)"}}
        segments, complete = sync(get, 1, known=old)
        self.assertFalse(complete)
        self.assertEqual(segments["8"], old["8"])
        self.assertIn("7", segments)

    def test_strava_time_formats(self):
        self.assertEqual([parse_time(t) for t in ("5:34", "1:02:03", "45s")], [334, 3723, 45])


if __name__ == "__main__":
    unittest.main()
