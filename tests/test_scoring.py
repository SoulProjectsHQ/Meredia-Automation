import unittest

from src.scoring.lead_score import MAX_POINTS, Priority, ScoreInput, classify, score_lead


class ClassifyTests(unittest.TestCase):
    def test_boundaries(self):
        cases = {
            100: Priority.HIGH_PRIORITY,
            80: Priority.HIGH_PRIORITY,
            79: Priority.GOOD_CANDIDATE,
            60: Priority.GOOD_CANDIDATE,
            59: Priority.LOWER_PRIORITY,
            40: Priority.LOWER_PRIORITY,
            39: Priority.DO_NOT_PRIORITIZE,
            0: Priority.DO_NOT_PRIORITIZE,
        }
        for score, expected in cases.items():
            with self.subTest(score=score):
                self.assertEqual(classify(score), expected)

    def test_out_of_range_rejected(self):
        for bad in (-1, 101):
            with self.assertRaises(ValueError):
                classify(bad)


class ScoreLeadTests(unittest.TestCase):
    def test_max_points_add_up_to_100(self):
        self.assertEqual(sum(MAX_POINTS.values()), 100)

    def test_full_score(self):
        result = score_lead(ScoreInput(**MAX_POINTS))
        self.assertEqual(result.total, 100)
        self.assertEqual(result.priority, Priority.HIGH_PRIORITY)

    def test_empty_score(self):
        result = score_lead(ScoreInput())
        self.assertEqual(result.total, 0)
        self.assertEqual(result.priority, Priority.DO_NOT_PRIORITIZE)

    def test_sum_and_breakdown(self):
        inp = ScoreInput(size_fit=15, clear_problem=14, financial_capacity=10, reachable_contact=12,
                         professional_firm=8, growth_or_change=3, improvement_potential=6)
        result = score_lead(inp)
        self.assertEqual(result.total, 68)
        self.assertEqual(result.priority, Priority.GOOD_CANDIDATE)
        self.assertEqual(result.breakdown["clear_problem"], 14)

    def test_category_above_max_is_rejected(self):
        with self.assertRaises(ValueError):
            score_lead(ScoreInput(growth_or_change=11))

    def test_negative_and_non_integer_rejected(self):
        with self.assertRaises(ValueError):
            score_lead(ScoreInput(size_fit=-1))
        with self.assertRaises(ValueError):
            score_lead(ScoreInput(size_fit=True))
        with self.assertRaises(ValueError):
            score_lead(ScoreInput(size_fit=10.5))


if __name__ == "__main__":
    unittest.main()
