from __future__ import annotations

import math
from unittest import TestCase

from apps.statistics.measures import (
    ContingencyTable,
    association_measures,
    chi_square,
    chi_square_p_value,
    log_likelihood,
)


class ContingencyTableTests(TestCase):
    def setUp(self) -> None:
        self.table = ContingencyTable.from_marginals(
            cooccurrence=20,
            node_opportunities=100,
            collocate_frequency=50,
            corpus_size=1000,
        )

    def test_builds_expected_observed_and_marginal_values(self) -> None:
        self.assertEqual(self.table.observed, (20.0, 80.0, 30.0, 870.0))
        self.assertEqual(self.table.expected, (5.0, 95.0, 45.0, 855.0))

    def test_rejects_impossible_values(self) -> None:
        with self.assertRaises(ValueError):
            ContingencyTable.from_marginals(
                cooccurrence=51,
                node_opportunities=100,
                collocate_frequency=50,
                corpus_size=1000,
            )

    def test_matches_hand_calculated_effect_sizes(self) -> None:
        measures = association_measures(self.table)
        self.assertAlmostEqual(measures["dice"], 40 / 150)
        self.assertAlmostEqual(measures["log_dice"], 14 + math.log2(40 / 150))
        self.assertAlmostEqual(measures["mi"], 2.0)
        self.assertAlmostEqual(measures["mi2"], math.log2(80.0))
        self.assertAlmostEqual(measures["mi3"], math.log2(1600.0))
        self.assertAlmostEqual(measures["minimum_sensitivity"], 0.2)
        self.assertAlmostEqual(measures["mu"], 4.0)
        self.assertAlmostEqual(measures["drf"], 0.2 - 30 / 900)
        self.assertAlmostEqual(measures["z_score"], 15 / math.sqrt(5))
        self.assertAlmostEqual(measures["t_score"], 15 / math.sqrt(20))

    def test_likelihood_helpers_are_finite_and_consistent(self) -> None:
        measures = association_measures(self.table)
        self.assertAlmostEqual(measures["chi_square"], chi_square(self.table))
        self.assertAlmostEqual(
            measures["log_likelihood"],
            log_likelihood(self.table),
        )
        self.assertGreater(measures["log_likelihood"], 0)
        self.assertLess(measures["p_value"], 0.001)

    def test_chi_square_survival_function(self) -> None:
        self.assertAlmostEqual(chi_square_p_value(3.841458820694124), 0.05, places=6)
