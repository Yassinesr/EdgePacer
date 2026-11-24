import unittest
from dataclasses import replace

from pacer import (Pacer, Rider, Section, beat, calibrate, choose, coeffs, effort_sections, sections_from_profile,
                   speed_for, wbal_low)

RIDER = Rider(72, cp=320)
CLIMB = sections_from_profile([0, 500, 1200, 1500, 2400, 3000], [500, 525, 581, 590, 671, 707])


def ride_time(sections, plan):
    return sum(s.length / v for s, (_, v) in zip(sections, plan))


class ProfileTest(unittest.TestCase):
    def test_sections_follow_the_grade(self):
        self.assertAlmostEqual(sum(s.length for s in CLIMB), 3000)
        self.assertTrue(all(50 <= s.length <= 200 for s in CLIMB))
        self.assertAlmostEqual(CLIMB[0].grade, 0.05)
        self.assertAlmostEqual(CLIMB[-1].grade, 0.06)

    def test_repeated_points_are_dropped_and_too_few_points_fail(self):
        secs = sections_from_profile([0, 500, 1000, 1000], [100, 125, 150, 150])
        self.assertAlmostEqual(sum(s.length for s in secs), 1000)
        for dist in ([], [0], [0, 0]):
            with self.assertRaises(ValueError):
                sections_from_profile(dist, [100] * len(dist))


class PlanTest(unittest.TestCase):
    def test_speed_inverts_power(self):
        for grade in (0.0, 0.05, 0.12):
            a, b = coeffs(Section(100, grade, 800), RIDER)
            v = speed_for(300, a, b)
            self.assertAlmostEqual(a * v + b * v ** 3, 300, places=3)

    def test_plan_hits_target_and_pushes_on_steep_parts(self):
        p = Pacer(CLIMB, RIDER, 570)
        self.assertAlmostEqual(ride_time(CLIMB, p.plan), 570, delta=0.1)
        steep = max(w for (w, _), s in zip(p.plan, CLIMB) if s.grade > 0.085)
        shallow = max(w for (w, _), s in zip(p.plan, CLIMB) if s.grade < 0.035)
        self.assertGreater(steep, shallow)

    def test_without_air_drag_the_rule_gives_even_power(self):
        powers = [w for w, _ in Pacer(CLIMB, replace(RIDER, cda=0), 570).plan]
        self.assertAlmostEqual(min(powers), max(powers), delta=0.01)

    def test_even_option(self):
        powers = [w for w, _ in Pacer(CLIMB, RIDER, 570, even=True).plan]
        self.assertAlmostEqual(min(powers), max(powers), delta=0.01)

    def test_steep_descent_coasts_at_the_speed_cap(self):
        (_, _), (w, v) = Pacer([Section(500, 0.06, 500), Section(300, -0.10, 520)], RIDER, 150).plan
        self.assertEqual(w, 0)
        self.assertAlmostEqual(v, 50 / 3.6)


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.p = Pacer(CLIMB, RIDER, 570)

    def test_on_schedule_mid_climb_keeps_the_same_targets(self):
        later = self.p.targets(1500, self.p.planned_time(1500))
        for (want, _), (_, got) in zip(self.p.plan[-len(later):], later):
            self.assertAlmostEqual(want, got, delta=0.5)

    def test_ahead_holds_the_plan_behind_pushes_impossible_is_none(self):
        planned = self.p.planned_time(1500)
        on_plan, _ = self.p.now(1500, planned)
        ahead, gap = self.p.now(1500, planned - 10)
        self.assertAlmostEqual(ahead, on_plan)
        self.assertAlmostEqual(gap, 10)
        behind, gap = self.p.now(1500, planned + 10)
        self.assertGreater(behind, on_plan)
        self.assertAlmostEqual(gap, -10)
        self.assertIsNone(self.p.now(1500, 569)[0])


class TargetTest(unittest.TestCase):
    def test_margin_is_one_percent_at_least_two_seconds(self):
        self.assertEqual(beat(600), 594)
        self.assertEqual(beat(100), 98)

    def test_w_prime_drains_above_cp_and_faster_targets_leave_less(self):
        self.assertAlmostEqual(wbal_low([(420, 100)], 320, 20000), 10000)
        lows = [Pacer(CLIMB, RIDER, t).w_low() for t in (600, 570, 540)]
        self.assertGreater(lows[0], lows[1])
        self.assertGreater(lows[1], lows[2])

    def test_ladder_drops_from_kom_to_pr_to_best_possible(self):
        self.assertEqual(choose(CLIMB, RIDER, [("KOM", 450), ("PR", 640)])[0], "PR")
        name, p = choose(CLIMB, RIDER, [("KOM", 450), ("PR", 460)])
        self.assertEqual(name, "Best possible")
        self.assertAlmostEqual(p.w_low(), 0, delta=50)  # J: ends with the reserve spent


class CalibrationTest(unittest.TestCase):
    def test_finds_the_factor_behind_a_recorded_effort(self):
        true = replace(RIDER, k=0.95)  # e.g. a power meter reading 5 % high
        t, d, p = [0.0], [0.0], [0.0]
        for s, (w, v) in zip(CLIMB, Pacer(CLIMB, true, 600).plan):
            for _ in range(10):
                t.append(t[-1] + s.length / v / 10)
                d.append(d[-1] + s.length / 10)
                p.append(w)
        t, d, p = t + [t[-1] + 5], d + [d[-1]], p + [0]  # stopped at the top: distance repeats
        self.assertAlmostEqual(calibrate(CLIMB, RIDER, [effort_sections(CLIMB, t, d, p)]), 0.95, places=3)


if __name__ == "__main__":
    unittest.main()
