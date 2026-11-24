import unittest

from pacer import Pacer, Rider, Section, power_at, sections_from_profile, speed_at

RIDER = Rider(72, 8)
CLIMB = sections_from_profile([0, 500, 1200, 1500, 2400, 3000], [500, 525, 581, 590, 671, 707])


def ride_time(plan):
    return sum(s.length / speed_at(p, s, RIDER) for s, p in plan)


class PacerTest(unittest.TestCase):
    def test_speed_inverts_power(self):
        for grade in (-0.03, 0.0, 0.05, 0.12):
            s = Section(100, grade, 800)
            self.assertAlmostEqual(power_at(speed_at(300, s, RIDER), s, RIDER), 300, places=3)

    def test_profile_resampling(self):
        self.assertAlmostEqual(sum(s.length for s in CLIMB), 3000)
        self.assertAlmostEqual(CLIMB[0].grade, 0.05)
        self.assertAlmostEqual(CLIMB[-1].grade, 0.06)

    def test_plan_hits_target_and_pushes_on_steep_parts(self):
        plan = Pacer(CLIMB, 570, RIDER).targets()
        self.assertAlmostEqual(ride_time(plan), 570, delta=0.1)
        steep = next(p for s, p in plan if s.grade > 0.085)
        flat = next(p for s, p in plan if s.grade < 0.035)
        self.assertGreater(steep, flat)

    def test_zero_bias_is_even_power(self):
        powers = [p for _, p in Pacer(CLIMB, 570, RIDER, bias=0).targets()]
        self.assertAlmostEqual(min(powers), max(powers))

    def test_on_schedule_mid_climb_keeps_the_same_targets(self):
        pacer = Pacer(CLIMB, 570, RIDER)
        plan = pacer.targets()
        elapsed = ride_time(plan[:15])  # exactly on plan after 1.5 km
        later = pacer.targets(done_m=1500, elapsed_s=elapsed)
        for (_, want), (_, got) in zip(plan[15:], later):
            self.assertAlmostEqual(want, got, delta=0.5)

    def test_behind_schedule_asks_for_more_and_impossible_is_none(self):
        pacer = Pacer(CLIMB, 570, RIDER)
        on_plan = pacer.targets(done_m=1500, elapsed_s=280)[0][1]
        behind = pacer.targets(done_m=1500, elapsed_s=300)[0][1]
        self.assertGreater(behind, on_plan)
        self.assertIsNone(pacer.targets(done_m=1500, elapsed_s=569))


if __name__ == "__main__":
    unittest.main()
