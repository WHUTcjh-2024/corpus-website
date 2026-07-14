from __future__ import annotations

from django import forms

from .engine import ParallelQuery, normalize_condition


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
    page_size = forms.ChoiceField(
        label="每页条数",
        choices=(("20", "20"), ("50", "50"), ("100", "100")),
        initial="50",
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

    def clean_search_side(self) -> str:
        return self.cleaned_data.get("search_side") or "zh"

    def clean_alignment_unit(self) -> str:
        return self.cleaned_data.get("alignment_unit") or self.default_alignment_unit

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1

    def to_query(self, cleaned: dict | None = None) -> ParallelQuery:
        values = cleaned if cleaned is not None else self.cleaned_data
        return ParallelQuery(
            q=values.get("q", ""),
            search_side=values.get("search_side", "zh"),
            zh_contains=values.get("zh_contains", ""),
            en_contains=values.get("en_contains", ""),
            zh_not_contains=values.get("zh_not_contains", ""),
            en_not_contains=values.get("en_not_contains", ""),
            alignment_unit=values.get("alignment_unit") or self.default_alignment_unit,
        )
