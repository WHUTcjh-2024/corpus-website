from __future__ import annotations

from django import forms

from .kwic import SORT_FIELDS, query_terms
from .query_parser import QuerySyntaxError, parse_query


LANGUAGE_LABELS = {"zh": "中文", "en": "English"}


class KwicSearchForm(forms.Form):
    query_mode = forms.ChoiceField(
        label="查询语法",
        choices=(("simple", "普通 KWIC"), ("cqp", "CQP 子集")),
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
        label="排序位置",
        required=False,
        choices=[("", "语料顺序"), *((value, value) for value in SORT_FIELDS)],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(
        min_value=1,
        initial=1,
        required=False,
        widget=forms.HiddenInput(),
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
        query = " ".join(self.cleaned_data["q"].split())
        return query

    def clean_query_mode(self) -> str:
        return self.cleaned_data.get("query_mode") or "simple"

    def clean_language(self) -> str:
        return self.cleaned_data.get("language") or self.available_languages[0]

    def clean(self) -> dict:
        cleaned = super().clean()
        query = cleaned.get("q", "")
        if query and not self.errors:
            try:
                if cleaned.get("query_mode") == "cqp":
                    parse_query(query, language=cleaned["language"])
                else:
                    detected_language, _ = query_terms(query)
                    if detected_language not in self.available_languages:
                        raise QuerySyntaxError("检索词语言不属于当前语料库。")
                    if detected_language != cleaned["language"]:
                        raise QuerySyntaxError("检索词语言与所选语言不一致。")
            except (QuerySyntaxError, ValueError) as exc:
                raise forms.ValidationError(str(exc), code="invalid_query") from exc
        return cleaned

    def clean_context(self) -> int:
        return self.cleaned_data.get("context") if self.cleaned_data.get("context") is not None else 5

    def clean_pos(self) -> str:
        return self.cleaned_data.get("pos", "").strip()

    def clean_page_size(self) -> int:
        return self.cleaned_data.get("page_size") or 50

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1
