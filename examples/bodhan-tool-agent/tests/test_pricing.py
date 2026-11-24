"""Unit tests for pricing calculations and budget tracking."""

import unittest
from bodhan_mcp.pricing import (
    BudgetExceededError,
    BudgetManager,
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
    calculate_translate_cost,
)


class TestPricing(unittest.TestCase):
    def test_cost_calculations(self):
        # Transcribe: ₹0.10 / min -> 60s is ₹0.10
        self.assertAlmostEqual(calculate_transcribe_cost(60.0), 0.10)
        self.assertAlmostEqual(calculate_transcribe_cost(30.0), 0.05)
        self.assertEqual(calculate_transcribe_cost(0.0), 0.0)

        # Translate: ₹0.20 per 10k output tokens
        self.assertAlmostEqual(calculate_translate_cost(10000), 0.20)
        self.assertAlmostEqual(calculate_translate_cost(5000), 0.10)

        # Speak: ₹6.00 per 10k input chars
        self.assertAlmostEqual(calculate_speak_cost(10000), 6.00)
        self.assertAlmostEqual(calculate_speak_cost(100), 0.06)

        # OCR: ₹0.20 per image
        self.assertAlmostEqual(calculate_ocr_cost(1), 0.20)
        self.assertAlmostEqual(calculate_ocr_cost(5), 1.00)

    def test_budget_manager_enforcement(self):
        bm = BudgetManager(max_cost_inr=1.0)
        self.assertEqual(bm.remaining_inr, 1.0)
        self.assertTrue(bm.can_afford(0.50))

        bm.record_cost("indic-ocr", 0.40, "2 images")
        self.assertAlmostEqual(bm.spent_inr, 0.40)
        self.assertAlmostEqual(bm.remaining_inr, 0.60)

        # Spending another 0.50 should succeed
        self.assertTrue(bm.can_afford(0.50))
        bm.check_budget(0.50)

        # Spending 0.70 should raise BudgetExceededError
        self.assertFalse(bm.can_afford(0.70))
        with self.assertRaises(BudgetExceededError):
            bm.check_budget(0.70)


if __name__ == "__main__":
    unittest.main()
