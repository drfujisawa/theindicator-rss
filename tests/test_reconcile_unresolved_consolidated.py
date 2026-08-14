import unittest

from scripts.maintenance import reconcile_unresolved_consolidated as reconciliation


class ReconcileUnresolvedConsolidatedTests(unittest.TestCase):
    def test_exact_title_recovers_verified_date_correction(self):
        ledger = [{
            "reference_date": "2018-03-15",
            "reference_title": "Buying A College Degree: Did Aunt Becky Overpay?",
        }]
        recovered, remaining = reconciliation.reconcile(
            ledger,
            {"2019-03-15"},
            {"buying a college degree: did aunt becky overpay?"},
        )
        self.assertEqual(recovered, ledger)
        self.assertEqual(remaining, [])

    def test_near_title_does_not_reconcile(self):
        ledger = [{"reference_date": "2018-01-01", "reference_title": "Episode One"}]
        recovered, remaining = reconciliation.reconcile(
            ledger, {"2019-01-01"}, {"episode one rebroadcast"}
        )
        self.assertEqual(recovered, [])
        self.assertEqual(remaining, ledger)


if __name__ == "__main__":
    unittest.main()
