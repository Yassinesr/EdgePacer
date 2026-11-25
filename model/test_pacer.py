import unittest
from dataclasses import replace

from pacer import (CAP, Pacer, Rider, Section, beat, choose, coeffs, entry_speed, fastest, ftp_out_of_date,
                   ride_time, section_times, sections_from_profile, speed_for)

RIDER = Rider(72, ftp=300)
CLIMB = sections_from_profile([0, 500, 1200, 1500, 2400, 3000], [500, 525, 581, 590, 671, 707])
LENGTHS, AB = [s.length for s in CLIMB], [coeffs(s, RIDER) for s in CLIMB]
BEST = fastest(LENGTHS, AB, RIDER)[1]


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


class RiderTest(unittest.TestCase):
    def test_absurd_garmin_profile_is_refused(self):
        for kg, ftp in ((72000, 300), (72, 0), (72, 5000)):  # grams by mistake, FTP unset, nonsense
            with self.assertRaises(ValueError):
                Rider(kg, ftp=ftp)

    def test_drag_area_grows_with_weight(self):
        self.assertAlmostEqual(RIDER.cda, 0.32)
        self.assertGreater(Rider(100, ftp=300).cda, RIDER.cda)

    def test_max_power_falls_to_ftp_at_one_hour_and_never_exceeds_1_5_ftp(self):
        self.assertAlmostEqual(RIDER.max_power(3600), 300)
        self.assertAlmostEqual(RIDER.max_power(7200), 300)
        self.assertGreater(RIDER.max_power(300), 300)
        self.assertEqual(RIDER.max_power(10), 450)  # the curve alone would allow 644 W in the last minute

    def test_reserve_drains_above_ftp_and_refills_below(self):
        full = RIDER.w_prime
        self.assertAlmostEqual(RIDER.w_after(full, 400, 60), full - 6000)
        self.assertGreater(RIDER.w_after(full - 6000, 200, 60), full - 6000)
        self.assertLess(RIDER.max_power(300, w_left=full / 4), RIDER.max_power(300))


class PhysicsTest(unittest.TestCase):
    def test_speed_inverts_power(self):
        for grade in (0.0, 0.05, 0.12):
            a, b = coeffs(Section(100, grade, 800), RIDER)
            v = speed_for(300, a, b)
            self.assertAlmostEqual(a * v + b * v ** 3, 300, places=3)

    def test_steep_descent_is_capped_at_50_kmh(self):
        self.assertEqual(speed_for(300, *coeffs(Section(300, -0.10, 500), RIDER)), CAP)

    def test_fastest_is_the_even_power_ftp_allows_for_that_long(self):
        watts, secs = fastest(LENGTHS, AB, RIDER)
        self.assertAlmostEqual(ride_time(watts, LENGTHS, AB), secs, delta=0.5)
        self.assertAlmostEqual(watts, RIDER.max_power(secs), delta=0.5)
        self.assertLess(fastest(LENGTHS, AB, replace(RIDER, ftp=330))[1], secs)


class TargetTest(unittest.TestCase):
    def test_margin_is_one_percent_at_least_two_seconds(self):
        self.assertEqual(beat(600), 594)
        self.assertEqual(beat(100), 98)

    def test_chases_kom_then_pr_then_fastest_and_skips_what_ftp_cannot_beat(self):
        names = lambda p: [p.name] + (names(p.next) if p.next else [])
        self.assertEqual(names(choose(CLIMB, RIDER, kom_s=BEST + 60, pr_s=BEST + 90)), ["KOM", "PR", "Fastest"])
        self.assertEqual(names(choose(CLIMB, RIDER, kom_s=BEST - 60, pr_s=BEST + 90)), ["PR", "Fastest"])
        self.assertEqual(names(choose(CLIMB, RIDER, kom_s=BEST - 60)), ["Fastest"])

    def test_ftp_out_of_date_is_only_a_warning(self):
        pr_s = BEST - 20  # a PR faster than the Garmin FTP says is possible
        self.assertTrue(ftp_out_of_date(RIDER, pr_s, RIDER.curve(pr_s) * 1.1))
        self.assertFalse(ftp_out_of_date(RIDER, pr_s, RIDER.curve(pr_s) * 0.9))
        self.assertFalse(ftp_out_of_date(RIDER, pr_s, None))  # estimated power is never used
        self.assertEqual(choose(CLIMB, RIDER, pr_s=pr_s).name, "Fastest")  # the FTP is not raised behind the rider's back

    def test_a_tired_rider_drops_a_target_sooner(self):
        p = choose(CLIMB, RIDER, pr_s=BEST + 30)
        at = p.planned_time(1500) + 8
        self.assertEqual(p.now(1500, at)[0], "PR")
        self.assertEqual(choose(CLIMB, RIDER, pr_s=BEST + 30).now(1500, at, w_left=0)[0], "Fastest")


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.p = Pacer(CLIMB, RIDER, 700)  # within reach, so there is room to catch up

    def test_even_power_hits_the_target(self):
        self.assertAlmostEqual(ride_time(self.p.power, LENGTHS, AB), 700, delta=0.1)

    def test_ahead_holds_the_plan_behind_pushes(self):
        planned = self.p.planned_time(1500)
        _, watts, gap = self.p.now(1500, planned - 10)
        self.assertEqual(watts, self.p.power)
        self.assertAlmostEqual(gap, 10)
        _, watts, gap = self.p.now(1500, planned + 10)
        self.assertGreater(watts, self.p.power)
        self.assertAlmostEqual(gap, -10)

    def test_catch_up_never_asks_more_than_the_ftp_allows(self):
        _, watts, _ = self.p.now(1500, self.p.planned_time(1500) + 200)  # e.g. after a long stop
        rest = [i for i in range(len(CLIMB)) if sum(LENGTHS[:i]) >= 1500 - 1e-6]
        cap, _ = fastest([LENGTHS[i] for i in rest], [AB[i] for i in rest], RIDER)
        self.assertAlmostEqual(watts, cap, delta=0.5)

    def test_no_watts_right_after_a_stop_but_the_gap_keeps_counting(self):
        planned = self.p.planned_time(1500)
        _, watts, gap = self.p.now(1500, planned + 40, since_stop_s=5)
        self.assertIsNone(watts)
        self.assertAlmostEqual(gap, -40)
        self.assertIsNotNone(self.p.now(1500, planned + 40, since_stop_s=15)[1])

    def test_after_steady_riding_watts_come_from_the_riders_own_power(self):
        planned = self.p.planned_time(1500)
        _, model_watts, _ = self.p.now(1500, planned)
        a, b = AB[9]  # the 9 % section that starts at 1500 m
        v = speed_for(model_watts, a, b)
        real = 1.1 * (a * v + b * v ** 3)  # this rider really needs 10 % more than the model says at that speed
        _, watts, _ = self.p.now(1500, planned, power=real, speed=v, steady_s=30)
        self.assertAlmostEqual(watts, 1.1 * model_watts, delta=0.5)
        self.assertEqual(self.p.now(1500, planned, power=real, speed=v, steady_s=5)[1], model_watts)  # not steady yet

    def test_gap_is_against_target_pace_not_the_pr_ride(self):
        # audit case: PR 600 s, target 594 s; 1 s behind target pace at halfway must read -1 s, not +2 s
        ghost = {"distance": [0, 3030], "time": [0, 600]}  # even pace, recorded 1 % long
        p = Pacer(CLIMB, RIDER, beat(600), ghost)
        self.assertAlmostEqual(p.now(1500, 297 + 1)[2], -1, delta=0.01)

    def test_out_of_reach_target_without_ghost_shows_behind(self):
        p = Pacer(CLIMB, RIDER, BEST - 60)  # audit case: used to read 0.0 s all the way
        on_capped_plan = ride_time(p.power, LENGTHS[:8], AB[:8])
        self.assertLess(p.now(sum(LENGTHS[:8]), on_capped_plan)[2], -5)

    def test_drops_to_the_next_target_for_good_once_out_of_reach(self):
        p = choose(CLIMB, RIDER, pr_s=BEST + 30)
        name, _, _ = p.now(1500, p.planned_time(1500) + 120)  # lost two minutes: the PR is gone
        self.assertEqual(name, "Fastest")
        self.assertEqual(p.now(1600, p.planned_time(1600))[0], "Fastest")  # even back on PR pace

    def test_falling_back_plans_the_fastest_afresh_from_here(self):
        p = choose(CLIMB, RIDER, pr_s=BEST + 30)
        name, watts, gap = p.now(1500, p.planned_time(1500) + 120)
        self.assertEqual(name, "Fastest")
        self.assertAlmostEqual(gap, 0, delta=0.5)  # not the bigger gap to the plan made at the start line
        rest = p.next
        self.assertAlmostEqual(sum(rest.lengths), 1500)
        self.assertAlmostEqual(watts, fastest(rest.lengths, rest.ab, RIDER)[0], delta=1)


class EntryTest(unittest.TestCase):
    def test_riding_in_at_ftp_is_a_flat_road_speed(self):
        self.assertTrue(9 < entry_speed(CLIMB, RIDER) < 12)

    def test_fast_entry_needs_fewer_watts_slow_entry_more(self):
        flying, still, standing = (Pacer(CLIMB, RIDER, 700, v0=v).power for v in (entry_speed(CLIMB, RIDER), None, 1.0))
        self.assertLess(flying, still)
        self.assertLess(still, standing)
        self.assertAlmostEqual(Pacer(CLIMB, RIDER, 700, v0=11.0).planned_time(3000), 700, delta=0.5)

    def test_a_fast_entry_coasts_down_as_a_fine_simulation_does(self):
        (a, b), m, v, p = AB[0], RIDER.inertia(), 11.0, 330.0
        t = x = 0.0
        while x < LENGTHS[0]:
            v1 = v + (p - a * v - b * v ** 3) / (m * v * v) * 0.05
            t, x, v = t + 0.05 / ((v + v1) / 2), x + 0.05, v1
        self.assertAlmostEqual(section_times(p, LENGTHS[:1], AB[:1], 11.0, m)[0], t, delta=0.3)


if __name__ == "__main__":
    unittest.main()
