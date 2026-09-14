from django.test import SimpleTestCase

from apps.search.filters import MatchOperator, QueryAttribute
from apps.search.query_parser import QuerySyntaxError, parse_query


class QueryParserBranchTests(SimpleTestCase):
    def test_parses_phrase_attributes_functions_wildcards_and_any_token(self) -> None:
        plan = parse_query(
            '"farm worker" [lemma="organize*"] [] starts_with(co) ends_with(tion) '
            "contains(oper) peasant?",
            language="en",
        )

        self.assertEqual(plan.source.count("  "), 0)
        self.assertEqual(len(plan.filters), 8)
        self.assertEqual(plan.filters[0].operator, MatchOperator.EXACT)
        self.assertEqual(plan.filters[2].attribute, QueryAttribute.LEMMA)
        self.assertEqual(plan.filters[2].operator, MatchOperator.WILDCARD)
        self.assertEqual(plan.filters[3].operator, MatchOperator.ANY)
        self.assertIn("starts_with", plan.description)

    def test_parses_chinese_phrase_and_bare_words(self) -> None:
        plan = parse_query('"农民 协会" 组织', language="zh")
        self.assertGreaterEqual(len(plan.filters), 3)
        self.assertTrue(
            all(item.attribute == QueryAttribute.WORD for item in plan.filters)
        )

    def test_rejects_global_query_constraints(self) -> None:
        cases = (
            ("", "不能为空"),
            ("x" * 501, "不能超过"),
            ("word", "查询语言"),
            ("word\x01", "控制字符"),
        )
        for query, message in cases:
            with self.subTest(query=query[:10]):
                language = "fr" if query == "word" else "en"
                with self.assertRaisesMessage(QuerySyntaxError, message):
                    parse_query(query, language=language)

    def test_rejects_unclosed_or_adjacent_groups(self) -> None:
        cases = (
            ('"unclosed', "双引号没有闭合"),
            ('""', "短语不能为空"),
            ('"farm"worker', "缺少空格"),
            ('[word="farm"', "方括号没有闭合"),
            ('[word="farm"]worker', "缺少空格"),
            ('contains("farm"', "函数括号没有闭合"),
            ("farm)", "保留符号"),
        )
        for query, message in cases:
            with self.subTest(query=query):
                with self.assertRaisesMessage(QuerySyntaxError, message):
                    parse_query(query, language="en")

    def test_rejects_bad_attribute_expressions_and_empty_wildcards(self) -> None:
        cases = (
            ('[title="farm"]', "属性条件格式"),
            ('[word="***"]', "属性通配符"),
            ("***", "通配符必须"),
        )
        for query, message in cases:
            with self.subTest(query=query):
                with self.assertRaisesMessage(QuerySyntaxError, message):
                    parse_query(query, language="en")

    def test_rejects_bad_functions_and_values(self) -> None:
        cases = (
            ("unknown(value)", "函数格式"),
            ("contains()", "函数格式"),
            ("contains(a;b)", "不允许的符号"),
            ("contains(" + "x" * 101 + ")", "单个查询值"),
        )
        for query, message in cases:
            with self.subTest(query=query[:20]):
                with self.assertRaisesMessage(QuerySyntaxError, message):
                    parse_query(query, language="en")

    def test_rejects_too_many_token_conditions(self) -> None:
        query = " ".join("word" for _ in range(51))
        with self.assertRaisesMessage(QuerySyntaxError, "最多包含"):
            parse_query(query, language="en")
