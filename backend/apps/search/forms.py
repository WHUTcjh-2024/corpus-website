from __future__ import annotations

from django import forms

from .kwic import SORT_FIELDS, KwicQueryError, compile_query, validate_full_regex
from .query_parser import QuerySyntaxError, parse_query


LANGUAGE_LABELS = {"zh": "中文", "en": "English"}


class KwicSearchForm(forms.Form):
    query_mode = forms.ChoiceField(
        label="查询语法",
        choices=(
            ("simple", "普通 KWIC"),
            ("full_regex", "全文正则"),
            ("cqp", "CQP 子集"),
        ),
        initial="simple",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    language = forms.ChoiceField(
        label="语言",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    q = forms.CharField(
        label="检索词、短语或表达式",
        max_length=500,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "数字经济 / \"high quality\" / [pos=\"NN1\"]",
                "autocomplete": "off",
            }
        ),
    )
    context = forms.IntegerField(
        label="左右窗口",
        min_value=0,
        max_value=50,
        initial=5,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 50}),
    )
    pos = forms.CharField(
        label="首词 POS（快捷）",
        max_length=30,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "如 n / NN1"}
        ),
    )
    page_size = forms.IntegerField(
        label="每页条数",
        min_value=1,
        max_value=100,
        initial=50,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 100}),
    )
    sort_by = forms.ChoiceField(
        label="一级排序",
        required=False,
        choices=[("", "语料顺序"), *((value, value) for value in SORT_FIELDS)],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_2 = forms.ChoiceField(
        label="二级排序",
        required=False,
        choices=[("", "不启用"), *((value, value) for value in SORT_FIELDS)],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_3 = forms.ChoiceField(
        label="三级排序",
        required=False,
        choices=[("", "不启用"), *((value, value) for value in SORT_FIELDS)],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_order = forms.ChoiceField(
        label="排序方式",
        required=False,
        initial="value",
        choices=(("value", "按值"), ("frequency", "按模式频次")),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    whole_words = forms.ChoiceField(
        label="匹配范围",
        required=False,
        initial="1",
        choices=(("1", "整词（Words）"), ("0", "词内子串")),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    case_sensitive = forms.BooleanField(
        label="区分大小写（Case）",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    regex = forms.BooleanField(
        label="逐 Token 正则（Regex）",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    page = forms.IntegerField(
        min_value=1,
        initial=1,
        required=False,
        widget=forms.HiddenInput(),
    )
    results_set = forms.ChoiceField(
        label="Results Set",
        choices=(("0", "全部命中"), ("25", "随机 25"), ("50", "随机 50"), ("100", "随机 100")),
        initial="0",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sample_seed = forms.IntegerField(
        label="随机种子",
        min_value=0,
        max_value=2_147_483_647,
        initial=0,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    hide_keyword = forms.BooleanField(
        label="隐藏检索项（教学）",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    query_list = forms.CharField(
        label="Search Query List",
        max_length=10000,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "每行一个备选查询"}),
    )
    context_queries = forms.CharField(
        label="Context Query List",
        max_length=10000,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "每行一个单 Token 语境词"}),
    )
    context_logic = forms.ChoiceField(
        label="Context logic",
        choices=(("or", "OR"), ("and", "AND")),
        initial="or",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    context_from = forms.IntegerField(
        label="From（L 为负数）",
        min_value=-10,
        max_value=10,
        initial=-5,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    context_to = forms.IntegerField(
        label="To（R 为正数）",
        min_value=-10,
        max_value=10,
        initial=5,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    exclude_context = forms.BooleanField(
        label="Not in context",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(
        self,
        *args,
        available_languages: tuple[str, ...],
        **kwargs,
    ) -> None:
        if not available_languages or any(
            language not in LANGUAGE_LABELS for language in available_languages
        ):
            raise ValueError("available_languages must contain zh and/or en.")
        self.available_languages = available_languages
        if args and args[0] is not None and "language" not in args[0]:
            data = args[0].copy()
            query = data.get("q", "")
            if query and data.get("query_mode", "simple") != "cqp":
                detected = (
                    "zh"
                    if any("\u4e00" <= char <= "\u9fff" for char in query)
                    else "en"
                )
                if detected in available_languages:
                    data["language"] = detected
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        self.fields["language"].choices = [
            (language, LANGUAGE_LABELS[language]) for language in available_languages
        ]
        self.fields["language"].initial = available_languages[0]

    def clean_q(self) -> str:
        query = self.cleaned_data["q"].strip()
        if self.cleaned_data.get("query_mode") == "full_regex":
            return query
        return " ".join(query.split())

    def clean_query_mode(self) -> str:
        return self.cleaned_data.get("query_mode") or "simple"

    def clean_language(self) -> str:
        return self.cleaned_data.get("language") or self.available_languages[0]

    def clean(self) -> dict:
        cleaned = super().clean()
        query = cleaned.get("q", "")
        query_mode = cleaned.get("query_mode")
        query_list = cleaned.get("query_list", ())
        context_queries = cleaned.get("context_queries", ())
        if (query_list or context_queries) and query_mode != "simple":
            raise forms.ValidationError(
                "查询词列表和语境搜索当前只能与普通 KWIC 模式组合使用。",
                code="incompatible_advanced_search",
            )
        if not query and not query_list and self.is_bound:
            self.add_error("q", "请提供主查询词或 Search Query List。")
        if cleaned.get("context_from", -5) > cleaned.get("context_to", 5):
            self.add_error("context_to", "语境窗口终点不能小于起点。")
        if query_mode == "cqp" and (
            cleaned.get("case_sensitive") or cleaned.get("regex")
        ):
            raise forms.ValidationError(
                "CQP 子集使用表达式自身的匹配规则，请关闭 Case/Regex 开关。",
                code="incompatible_options",
            )
        if query_mode == "full_regex":
            if cleaned.get("pos"):
                raise forms.ValidationError(
                    "全文正则不能同时使用首词 POS 条件。",
                    code="incompatible_options",
                )
            cleaned["whole_words"] = False
            cleaned["regex"] = True
        if query and not self.errors:
            try:
                if query_mode == "cqp":
                    parse_query(query, language=cleaned["language"])
                elif query_mode == "full_regex":
                    validate_full_regex(
                        query,
                        case_sensitive=cleaned.get("case_sensitive", False),
                    )
                else:
                    compile_query(
                        query,
                        language=cleaned["language"],
                        whole_words=cleaned.get("whole_words", True),
                        case_sensitive=cleaned.get("case_sensitive", False),
                        regex=cleaned.get("regex", False),
                    )
            except (QuerySyntaxError, KwicQueryError, ValueError) as exc:
                raise forms.ValidationError(str(exc), code="invalid_query") from exc
        return cleaned

    def clean_whole_words(self) -> bool:
        value = self.cleaned_data.get("whole_words")
        return value != "0"

    def clean_context(self) -> int:
        return self.cleaned_data.get("context") if self.cleaned_data.get("context") is not None else 5

    def clean_pos(self) -> str:
        return self.cleaned_data.get("pos", "").strip()

    def clean_page_size(self) -> int:
        return self.cleaned_data.get("page_size") or 50

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1

    def clean_results_set(self) -> int:
        return int(self.cleaned_data.get("results_set") or 0)

    def clean_sample_seed(self) -> int:
        return self.cleaned_data.get("sample_seed") or 0

    def clean_query_list(self) -> tuple[str, ...]:
        return self._clean_query_lines("query_list")

    def clean_context_queries(self) -> tuple[str, ...]:
        return self._clean_query_lines("context_queries")

    def clean_context_logic(self) -> str:
        return self.cleaned_data.get("context_logic") or "or"

    def clean_context_from(self) -> int:
        value = self.cleaned_data.get("context_from")
        return -5 if value is None else value

    def clean_context_to(self) -> int:
        value = self.cleaned_data.get("context_to")
        return 5 if value is None else value

    def _clean_query_lines(self, field_name: str) -> tuple[str, ...]:
        value = self.cleaned_data.get(field_name, "")
        lines = tuple(
            dict.fromkeys(
                normalized
                for line in value.splitlines()
                if (normalized := " ".join(line.split()))
            )
        )
        if len(lines) > 100:
            raise forms.ValidationError("列表最多包含 100 项。", code="too_many_queries")
        return lines
