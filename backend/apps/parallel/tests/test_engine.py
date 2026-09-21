from __future__ import annotations

import sqlite3

from django.test import SimpleTestCase

from apps.parallel.engine import _alignment_quality_summary


class AlignmentQualitySummaryTests(SimpleTestCase):
    def test_source_gap_is_not_reported_as_verified_alignment(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE parallel_pairs (
                method TEXT NOT NULL,
                confidence REAL NOT NULL,
                zh_text TEXT NOT NULL,
                en_text TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO parallel_pairs VALUES (?, ?, ?, ?)",
            (
                ("provided_structure_id", 1.0, "中文", "English"),
                ("provided_structure_id", 1.0, "", "English only"),
                ("provided_structure_order", 1.0, "顺序", "Ordered"),
                ("automatic_length_dp_1_1", 0.8, "自动", "Automatic"),
            ),
        )

        summary = _alignment_quality_summary(
            connection,
            where_sql="1 = 1",
            parameters=(),
        )

        self.assertEqual(summary.pair_total, 4)
        self.assertEqual(summary.verified_count, 1)
        self.assertEqual(summary.ordered_count, 1)
        self.assertEqual(summary.automatic_count, 1)
        self.assertEqual(summary.review_count, 1)
        self.assertEqual(summary.gap_count, 1)
