import unittest

from ui_components import _confidence_label_and_score


class ConfidenceDirectionTests(unittest.TestCase):
    def test_strong_over_signal_scores_as_high_confidence_over(self):
        label, score, direction = _confidence_label_and_score(
            edge=3.0,
            hit_ratio=0.80,
            osc_class="Baixa",
            matchup_label="Favorável",
            form_signal="↗ Em alta",
            inj_status="Available",
        )
        self.assertEqual(direction, "OVER")
        self.assertGreaterEqual(score, 5)
        self.assertIn("Alta", label)

    def test_strong_under_signal_scores_as_high_confidence_under(self):
        label, score, direction = _confidence_label_and_score(
            edge=-3.0,
            hit_ratio=0.20,
            osc_class="Baixa",
            matchup_label="Difícil",
            form_signal="↘ Em queda",
            inj_status="Available",
        )
        self.assertEqual(direction, "UNDER")
        self.assertGreaterEqual(score, 5)
        self.assertIn("Alta", label)


if __name__ == "__main__":
    unittest.main()
