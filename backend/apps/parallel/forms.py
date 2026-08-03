from __future__ import annotations

from django import forms

from .engine import ParallelQuery, normalize_condition


SORT_CHOICES = (
    ("", "不排序"),
    ("CEN", "命中词 CEN"),
    ("L1", "左 1 词"),
    ("L2", "左 2 词"),
    ("L3", "左 3 词"),
    ("L4", "左 4 词"),
    ("L5", "左 5 词"),
    ("R1", "右 1 词"),
    ("R2", "右 2 词"),
    ("R3", "右 3 词"),
    ("R4", "右 4 词"),
    ("R5", "右 5 词"),
)


class ParallelSearchForm(forms.Form):
    q = forms.CharField(
        label="主检索词",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "输入中文原文或英文译文中的词/短语",
                "autocomplete": "off",
            }
        ),
    )
    search_side = forms.ChoiceField(
        label="检索方向",
        choices=(("zh", "中文 → 英文"), ("en", "英文 → 中文")),
        initial="zh",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    alignment_unit = forms.ChoiceField(
        label="对齐单元",
        choices=(("sentence", "句子"), ("paragraph", "段落")),
        initial="sentence",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    zh_contains = forms.CharField(
        label="中文同时包含",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    en_contains = forms.CharField(
        label="英文同时包含",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    zh_not_contains = forms.CharField(
        label="中文排除",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    en_not_contains = forms.CharField(
        label="英文排除",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    filename_contains = forms.CharField(
        label="来源文件包含",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "作者、时期或文件名片段"}
        ),
    )
    min_confidence = forms.DecimalField(
        label="最低对齐置信度",
        min_value=0,
        max_value=1,
        decimal_places=2,
        initial=0,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.05"}),
    )
    whole_words = forms.BooleanField(
        label="完整词匹配（优先使用源语料词界）",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    case_sensitive = forms.BooleanField(
        label="区分大小写",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    infer_target_highlights = forms.BooleanField(
        label="显示目标侧统计提示（实验性，不代表词语对齐）",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    nth_entry = forms.IntegerField(
        label="每 N 条显示一条",
        min_value=1,
        max_value=1_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    sort_1 = forms.ChoiceField(
        label="一级排序",
        choices=(("", "语料顺序"), *SORT_CHOICES[1:]),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_2 = forms.ChoiceField(
        label="二级排序",
        choices=SORT_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_3 = forms.ChoiceField(
        label="三级排序",
        choices=SORT_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页条数",
        choices=(("20", "20"), ("50", "50"), ("100", "100")),
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    context_size = forms.ChoiceField(
        label="上下文",
        choices=(("10", "10"), ("20", "20"), ("50", "50"), ("100", "100")),
        initial="20",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())

    def __init__(
        self,
        *args,
        default_alignment_unit: str = "sentence",
        available_alignment_units: tuple[str, ...] = ("sentence", "paragraph"),
        **kwargs,
    ) -> None:
        if default_alignment_unit not in {"sentence", "paragraph"}:
            raise ValueError("default_alignment_unit must be sentence or paragraph.")
        if not available_alignment_units or any(
            unit not in {"sentence", "paragraph"} for unit in available_alignment_units
        ):
            raise ValueError("available_alignment_units contains an invalid unit.")
        if default_alignment_unit not in available_alignment_units:
            raise ValueError("default_alignment_unit must be available.")
        self.default_alignment_unit = default_alignment_unit
        super().__init__(*args, **kwargs)
        self.fields["alignment_unit"].initial = default_alignment_unit
        labels = {"sentence": "句子", "paragraph": "段落"}
        self.fields["alignment_unit"].choices = [
            (unit, labels[unit]) for unit in available_alignment_units
        ]

    def clean(self) -> dict:
        cleaned = super().clean()
        for name in (
            "q",
            "zh_contains",
            "en_contains",
            "zh_not_contains",
            "en_not_contains",
            "filename_contains",
        ):
            cleaned[name] = normalize_condition(cleaned.get(name, ""))
        if not self.errors:
            query = self.to_query(cleaned)
            try:
                query.validate()
            except ValueError as exc:
                raise forms.ValidationError(str(exc), code="invalid_query") from exc
        return cleaned

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_context_size(self) -> int:
        return int(self.cleaned_data.get("context_size") or 20)

    def clean_search_side(self) -> str:
        return self.cleaned_data.get("search_side") or "zh"

    def clean_alignment_unit(self) -> str:
        return self.cleaned_data.get("alignment_unit") or self.default_alignment_unit

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1

    def clean_nth_entry(self) -> int:
        return self.cleaned_data.get("nth_entry") or 1

    def to_query(self, cleaned: dict | None = None) -> ParallelQuery:
        values = cleaned if cleaned is not None else self.cleaned_data
        return ParallelQuery(
            q=values.get("q", ""),
            search_side=values.get("search_side", "zh"),
            zh_contains=values.get("zh_contains", ""),
            en_contains=values.get("en_contains", ""),
            zh_not_contains=values.get("zh_not_contains", ""),
            en_not_contains=values.get("en_not_contains", ""),
            filename_contains=values.get("filename_contains", ""),
            min_confidence=float(values.get("min_confidence") or 0),
            alignment_unit=values.get("alignment_unit") or self.default_alignment_unit,
            whole_words=bool(values.get("whole_words", False)),
            case_sensitive=bool(values.get("case_sensitive", False)),
            infer_target_highlights=bool(values.get("infer_target_highlights", False)),
            sort_1=values.get("sort_1", ""),
            sort_2=values.get("sort_2", ""),
            sort_3=values.get("sort_3", ""),
            context_size=values.get("context_size") or 20,
            nth_entry=values.get("nth_entry") or 1,
        )
