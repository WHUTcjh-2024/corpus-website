from django.test import SimpleTestCase

from apps.search.filters import MatchOperator, compile_token_filter
from apps.search.query_parser import parse_query


class OpenSlotQueryTests(SimpleTestCase):
    def test_empty_brackets_compile_to_one_safe_token_slot(self) -> None:
        plan = parse_query('[word="shared"] [] [word="future"]', language="en")

        self.assertEqual(len(plan.filters), 3)
        self.assertEqual(plan.filters[1].operator, MatchOperator.ANY)
        self.assertEqual(
            compile_token_filter(plan.filters[1], alias="t1", language="en"),
            ("1 = 1", []),
        )
