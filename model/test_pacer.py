import unittest
from dataclasses import replace

from pacer import (CAP, Pacer, Rider, Section, beat, calibrate, choose, coeffs, effort_sections, fastest, ride_time,
                   sections_from_profile, speed_for)

RIDER = Rider(72, ftp=300)
CLIMB = sections_from_profile([0, 500, 1200, 1500, 2400, 3000], [500, 525, 581, 590, 671, 707])
LENGTHS, AB = [s.length for s in CLIMB], [coeffs(s, RIDER) for s in CLIMB]


class ProfileTest(unittest.TestCase):
    def test_sections_follow_the_grade(self):
        self.assertAlmostEqual(sum(LENGTHS), 3000)
        self.assertTrue(all(50 <= s.length <= 200 for s in CLIMB))
        self.assertAlmostEqual(CLIMB[0].grade, 0.05)
        self.assertAlmostEqual(CLIMB[-1].grade, 0.06)

    def test_repeated_points_are_dropped_and_too_few_points_fail(self):
        secs = sections_from_profile([0, 500, 1000, 1000], [100, 125, 150, 150])
        self.assertAlmostEqual(sum(s.length for s in secs), 1000)
        for dist in ([], [0], [0, 0]):
            with self.assertRaises(ValueError):
                sections_from_profile(dist, [100] * len(dist))


class PhysicsTest(unittest.TestCase):
    def test_speed_inverts_power(self):
        for grade in (0.0, 0.05, 0.12):
            a, b = coeffs(Section(100, grade, 800), RIDER)
            v = speed_for(300, a, b)
            self.assertAlmostEqual(a * v + b * v ** 3, 300, places=3)

    def test_steep_descent_is_capped_at_50_kmh(self):
        self.assertEqual(speed_for(300, *coeffs(Section(300, -0.10, 500), RIDER)), CAP)

    def test_drag_area_grows_with_weight(self):
        self.assertAlmostEqual(RIDER.cda, 0.32)
        self.assertGreater(Rider(100, ftp=300).cda, RIDER.cda)


class TargetTest(unittest.TestCase):
    def test_margin_is_one_percent_at_least_two_seconds(self):
        self.assertEqual(beat(600), 594)
        self.assertEqual(beat(100), 98)

    def test_fastest_is_the_even_power_ftp_allows_for_that_long(self):
        watts, secs = fastest(LENGTHS, AB, RIDER)
        self.assertAlmostEqual(ride_time(watts, LENGTHS, AB), secs, delta=0.5)
        self.assertAlmostEqual(watts, RIDER.max_power(secs), delta=0.5)
        self.assertLess(fastest(LENGTHS, AB, replace(RIDER, ftp=330))[1], secs)

    def test_chases_kom_if_reachable_else_pr_else_fastest(self):
        _, best = fastest(LENGTHS, AB, RIDER)
        self.assertEqual(choose(CLIMB, RIDER, kom_s=best + 60, pr_s=best + 90)[0], "KOM")
        self.assertEqual(choose(CLIMB, RIDER, kom_s=best - 60, pr_s=best + 90)[0], "PR")
        name, p = choose(CLIMB, RIDER, kom_s=best - 60)
        self.assertEqual(name, "Fastest")
        self.assertAlmostEqual(p.target_s, best)

    def test_out_of_reach_target_shows_the_best_the_ftp_allows(self):
        self.assertAlmostEqual(Pacer(CLIMB, RIDER, 300).power, fastest(LENGTHS, AB, RIDER)[0], delta=0.5)


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.p = Pacer(CLIMB, RIDER, 700)  # within reach, so there is room to catch up

    def test_even_power_hits_the_target(self):
        self.assertAlmostEqual(ride_time(self.p.power, LENGTHS, AB), 700, delta=0.1)

    def test_ahead_holds_the_plan_behind_pushes(self):
        planned = self.p.planned_time(1500)
        watts, gap = self.p.now(1500, planned - 10)
        self.assertEqual(watts, self.p.power)
        self.assertAlmostEqual(gap, 10)
        watts, gap = self.p.now(1500, planned + 10)
        self.assertGreater(watts, self.p.power)
        self.assertAlmostEqual(gap, -10)

    def test_catch_up_never_asks_more_than_the_ftp_allows(self):
        watts, _ = self.p.now(1500, self.p.planned_time(1500) + 200)  # e.g. after a long stop
        rest = [i for i, _ in enumerate(CLIMB) if sum(LENGTHS[:i]) >= 1500 - 1e-6]
        cap, _ = fastest([LENGTHS[i] for i in rest], [AB[i] for i in rest], RIDER)
        self.assertAlmostEqual(watts, cap, delta=0.5)

    def test_gap_is_measured_against_the_pr_ride(self):
        ghost = {"distance": [0, 1515, 3030], "time": [0, 250, 590]}  # recorded 1 % long
        p = Pacer(CLIMB, RIDER, 584, ghost)
        self.assertAlmostEqual(p.now(1500, 255)[1], -5)


class CalibrationTest(unittest.TestCase):
    def test_finds_the_factor_behind_a_recorded_effort(self):
        true = replace(RIDER, k=0.95)  # e.g. a power meter reading 5 % high
        p = Pacer(CLIMB, true, 650)
        t, d, w = [0.0], [0.0], [0.0]
        for s, coef in zip(CLIMB, p.ab):
            v = speed_for(p.power, *coef)
            for _ in range(10):
                t.append(t[-1] + s.length / v / 10)
                d.append(d[-1] + s.length / 10)
                w.append(p.power)
        t, d, w = t + [t[-1] + 5], d + [d[-1]], w + [0]  # stopped at the top: distance repeats
        self.assertAlmostEqual(calibrate(CLIMB, RIDER, [effort_sections(CLIMB, t, d, w)]), 0.95, places=3)


if __name__ == "__main__":
    unittest.main()
