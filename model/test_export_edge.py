import unittest

from export_edge import example, export

LL = [[32.0 + i * 0.0001, 118.8] for i in range(41)]  # ~11 m apart, 440 m
PROFILE = {"distance": [i * 11.0 for i in range(41)], "altitude": [10 + i * 0.6 for i in range(41)], "latlng": LL}


class ExportTest(unittest.TestCase):
    def test_only_paceable_climbs_go_to_the_edge(self):
        segs = {
            "climb": {"name": "Wall", "avg_grade": 5.5, "kom_s": 81, "qom_s": 100, "pr_s": 120, "profile": PROFILE,
                      "ghost": {"distance": [0, 200, 200, 440], "time": [0, 50, 55, 120]}, "pr_watts": 310.0},
            "flat": {"name": "Flat", "avg_grade": 0.5, "kom_s": 60, "profile": PROFILE},
            "danger": {"name": "Descent", "avg_grade": 5.0, "hazardous": True, "profile": PROFILE},
            "no_gps": {"name": "No GPS", "avg_grade": 5.0, "profile": {**PROFILE, "latlng": []}},
        }
        out = export(segs, 1700000000)
        self.assertEqual([c["name"] for c in out["climbs"]], ["Wall"])
        c = out["climbs"][0]
        self.assertEqual(len(c["path"]), 2 * len(c["pd"]))  # flat east, north pairs, one per distance
        self.assertEqual(c["o"], [32000000, 118800000])
        self.assertEqual(c["path"][:2], [0.0, 0.0])
        self.assertAlmostEqual(c["path"][-1], 40 * 0.0001 * 111320, places=0)  # due north, 4 hundredths of a degree
        self.assertAlmostEqual(c["pd"][-1], 440)
        self.assertAlmostEqual(sum(c["sec"][0::3]), 440)  # section lengths cover the climb
        self.assertEqual(c["ghost"], [0, 0, 200, 50, 440, 120])  # the stop at 200 m is dropped
        self.assertEqual((c["kom"], c["qom"], c["pr"], c["prw"]), (81, 100, 120, 310.0))

    def test_example_climb_exports(self):
        out = export(example(), 0)
        self.assertEqual(out["climbs"][0]["name"], "Test climb")
        self.assertAlmostEqual(out["climbs"][0]["pd"][-1], 3000)


if __name__ == "__main__":
    unittest.main()
