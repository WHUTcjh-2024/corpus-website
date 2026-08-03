from __future__ import annotations

import re

from django import forms

from apps.search.kwic import query_terms


LANGUAGE_LABELS = {"zh": "中文", "en": "English"}
PAGE_SIZE_CHOICES = (("20", "20"), ("50", "50"), ("100", "100"))


class LanguageForm(forms.Form):
    language = forms.ChoiceField(label="语言", widget=forms.Select(attrs={"class": "form-select"}))

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
        super().__init__(*args, **kwargs)
        self.fields["language"].choices = [
            (language, LANGUAGE_LABELS[language]) for language in available_languages
        ]
        self.fields["language"].initial = available_languages[0]


class WordListForm(LanguageForm):
    display_type = forms.ChoiceField(
        label="Display type",
        choices=(("type", "Type"), ("type_pos", "Type + POS"), ("headword", "Headword/Lemma")),
        initial="type",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    min_frequency = forms.IntegerField(
        label="最小频次",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_range = forms.IntegerField(
        label="最小文档数",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    filter = forms.CharField(
        label="词项过滤",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "包含文本"}),
    )
    pos = forms.CharField(
        label="POS",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "如 n / NN1"}),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort_by = forms.ChoiceField(
        label="排序",
        choices=(("frequency", "Frequency"), ("range", "Range"), ("term", "Word start"), ("end", "Word end")),
        initial="frequency",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页",
        choices=PAGE_SIZE_CHOICES,
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())
    case_sensitive = forms.BooleanField(
        label="区分大小写",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    invert_order = forms.BooleanField(
        label="反向排序",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    list_mode = forms.ChoiceField(
        label="名单过滤",
        choices=(("none", "不启用"), ("stop", "停用词"), ("allow", "仅允许词")),
        initial="none",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    list_terms = forms.CharField(
        label="词项名单",
        max_length=4000,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "空格、逗号或换行分隔"}),
    )

    def clean_filter(self) -> str:
        return " ".join(self.cleaned_data.get("filter", "").split())

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 1

    def clean_min_range(self) -> int:
        return self.cleaned_data.get("min_range") or 1

    def clean_pos(self) -> str:
        return self.cleaned_data.get("pos", "").strip()

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "frequency"

    def clean_display_type(self) -> str:
        return self.cleaned_data.get("display_type") or "type"

    def clean_list_mode(self) -> str:
        return self.cleaned_data.get("list_mode") or "none"

    def clean_list_terms(self) -> tuple[str, ...]:
        value = self.cleaned_data.get("list_terms", "")
        terms = tuple(dict.fromkeys(term for term in re.split(r"[\s,，、;；]+", value) if term))
        if len(terms) > 500:
            raise forms.ValidationError("名单最多包含 500 个词项。", code="too_many_list_terms")
        return terms

    def clean(self) -> dict:
        cleaned = super().clean()
        if cleaned.get("list_mode") != "none" and not cleaned.get("list_terms"):
            self.add_error("list_terms", "启用名单过滤后必须提供至少一个词项。")
        return cleaned

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1


class NgramForm(LanguageForm):
    n = forms.ChoiceField(
        label="N-Gram size",
        choices=tuple((str(value), str(value)) for value in range(2, 6)),
        initial="2",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    open_slot = forms.ChoiceField(
        label="开放槽位",
        choices=(("0", "无"),) + tuple((str(value), f"S{value}") for value in range(1, 6)),
        initial="0",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    min_frequency = forms.IntegerField(
        label="最小频次",
        min_value=1,
        max_value=1_000_000,
        initial=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_range = forms.IntegerField(
        label="最小文档数",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    filter = forms.CharField(
        label="N-Gram 过滤",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort_by = forms.ChoiceField(
        label="排序",
        choices=(("frequency", "Frequency"), ("range", "Range"), ("term", "N-Gram")),
        initial="frequency",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页",
        choices=PAGE_SIZE_CHOICES,
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())

    def clean_n(self) -> int:
        return int(self.cleaned_data.get("n") or 2)

    def clean(self) -> dict:
        cleaned = super().clean()
        if (cleaned.get("open_slot") or 0) > (cleaned.get("n") or 2):
            self.add_error("open_slot", "开放槽位不能超过 N-Gram 长度。")
        return cleaned

    def clean_open_slot(self) -> int:
        return int(self.cleaned_data.get("open_slot") or 0)

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 2

    def clean_min_range(self) -> int:
        return self.cleaned_data.get("min_range") or 1

    def clean_filter(self) -> str:
        return " ".join(self.cleaned_data.get("filter", "").split())

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "frequency"

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1


class ClusterForm(LanguageForm):
    q = forms.CharField(
        label="Search term",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    cluster_size = forms.ChoiceField(
        label="Cluster size",
        choices=tuple((str(value), str(value)) for value in range(2, 11)),
        initial="3",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    query_position = forms.ChoiceField(
        label="Term position",
        choices=(("left", "On left"), ("right", "On right")),
        initial="left",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    min_frequency = forms.IntegerField(
        label="Min. Freq",
        min_value=1,
        max_value=1_000_000,
        initial=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_range = forms.IntegerField(
        label="Min. Range",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort_by = forms.ChoiceField(
        label="Sort by",
        choices=(
            ("frequency", "Frequency"),
            ("range", "Range"),
            ("term", "Cluster"),
            ("probability", "Transition probability"),
        ),
        initial="frequency",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页",
        choices=PAGE_SIZE_CHOICES,
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())

    def clean_cluster_size(self) -> int:
        return int(self.cleaned_data.get("cluster_size") or 3)

    def clean_query_position(self) -> str:
        return self.cleaned_data.get("query_position") or "left"

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 2

    def clean_min_range(self) -> int:
        return self.cleaned_data.get("min_range") or 1

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "frequency"

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1


class KeywordForm(LanguageForm):
    reference_corpus = forms.ChoiceField(
        label="参照语料库",
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    min_frequency = forms.IntegerField(
        label="任一语料最小频次",
        min_value=1,
        max_value=1_000_000,
        initial=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_range = forms.IntegerField(
        label="任一语料最小文档数",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    filter = forms.CharField(
        label="词项过滤",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    include_negative = forms.BooleanField(
        label="显示负关键词",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort_by = forms.ChoiceField(
        label="排序",
        choices=(
            ("log_likelihood", "Log-Likelihood"),
            ("chi_square", "Chi-square"),
            ("log_ratio", "|Log Ratio|"),
            ("frequency", "Target Frequency"),
            ("term", "Word"),
        ),
        initial="log_likelihood",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页",
        choices=PAGE_SIZE_CHOICES,
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())

    def __init__(self, *args, reference_corpora, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reference_languages = {
            str(corpus_id): languages for corpus_id, _, languages in reference_corpora
        }
        self.fields["reference_corpus"].choices = [
            (str(corpus_id), name) for corpus_id, name, _ in reference_corpora
        ]
        if reference_corpora:
            self.fields["reference_corpus"].initial = str(reference_corpora[0][0])

    def clean(self) -> dict:
        cleaned = super().clean()
        reference_id = cleaned.get("reference_corpus", "")
        language = cleaned.get("language", "")
        if reference_id and language not in self.reference_languages.get(reference_id, ()):
            raise forms.ValidationError(
                "参照语料不包含所选语言。",
                code="reference_language_mismatch",
            )
        return cleaned

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 2

    def clean_min_range(self) -> int:
        return self.cleaned_data.get("min_range") or 1

    def clean_filter(self) -> str:
        return " ".join(self.cleaned_data.get("filter", "").split())

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "log_likelihood"

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1


class WordcloudForm(LanguageForm):
    min_frequency = forms.IntegerField(
        label="最小频次",
        min_value=1,
        max_value=1_000_000,
        initial=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    max_words = forms.ChoiceField(
        label="最大词数",
        choices=(("25", "25"), ("50", "50"), ("100", "100"), ("200", "200")),
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    theme = forms.ChoiceField(
        label="配色",
        choices=(("ocean", "深海蓝"), ("forest", "森林绿"), ("sunset", "落日橙")),
        initial="ocean",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    stopwords = forms.CharField(
        label="停用词",
        max_length=2000,
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "使用空格、逗号或换行分隔",
            }
        ),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 2

    def clean_max_words(self) -> int:
        return int(self.cleaned_data.get("max_words") or 50)

    def clean_theme(self) -> str:
        return self.cleaned_data.get("theme") or "ocean"

    def clean_stopwords(self) -> tuple[str, ...]:
        value = self.cleaned_data.get("stopwords", "")
        terms = tuple(dict.fromkeys(term for term in re.split(r"[\s,，、;；]+", value) if term))
        if len(terms) > 200:
            raise forms.ValidationError("停用词不能超过 200 个。", code="too_many_stopwords")
        return terms


class CollocateForm(LanguageForm):
    q = forms.CharField(
        label="Search term",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    left_span = forms.IntegerField(
        label="Left span",
        min_value=0,
        max_value=10,
        initial=5,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    right_span = forms.IntegerField(
        label="Right span",
        min_value=0,
        max_value=10,
        initial=5,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_frequency = forms.IntegerField(
        label="最小共现",
        min_value=1,
        max_value=1_000_000,
        initial=2,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    min_range = forms.IntegerField(
        label="最小文档数",
        min_value=1,
        max_value=1_000_000,
        initial=1,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    pos = forms.CharField(
        label="Collocate POS",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    include_punctuation = forms.BooleanField(
        label="包含标点",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort_by = forms.ChoiceField(
        label="Statistic",
        choices=(
            ("log_dice", "LogDice"),
            ("mi", "MI"),
            ("mi2", "MI2"),
            ("mi3", "MI3"),
            ("dice", "Dice"),
            ("minimum_sensitivity", "Minimum Sensitivity"),
            ("mu", "Mu"),
            ("rrf", "RRF"),
            ("drf", "DRF"),
            ("z_score", "Z-score"),
            ("t_score", "T-score"),
            ("log_ratio", "Log Ratio"),
            ("log_likelihood", "Log-Likelihood"),
            ("chi_square", "Chi-square"),
            ("frequency", "Frequency"),
            ("left_frequency", "Frequency (L)"),
            ("right_frequency", "Frequency (R)"),
            ("range", "Range"),
            ("term", "Word"),
        ),
        initial="log_dice",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page_size = forms.ChoiceField(
        label="每页",
        choices=PAGE_SIZE_CHOICES,
        initial="50",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    page = forms.IntegerField(min_value=1, initial=1, required=False, widget=forms.HiddenInput())

    def clean(self) -> dict:
        cleaned = super().clean()
        query = " ".join(cleaned.get("q", "").split())
        cleaned["q"] = query
        if query and not self.errors:
            try:
                detected_language, _ = query_terms(query)
            except ValueError as exc:
                raise forms.ValidationError(str(exc), code="invalid_query") from exc
            if detected_language != cleaned.get("language"):
                raise forms.ValidationError(
                    "检索词语言与所选语言不一致。",
                    code="language_mismatch",
                )
        if (cleaned.get("left_span") or 0) == 0 and (cleaned.get("right_span") or 0) == 0:
            raise forms.ValidationError("左右窗口不能同时为 0。", code="empty_span")
        return cleaned

    def clean_left_span(self) -> int:
        value = self.cleaned_data.get("left_span")
        return 5 if value is None else value

    def clean_right_span(self) -> int:
        value = self.cleaned_data.get("right_span")
        return 5 if value is None else value

    def clean_min_frequency(self) -> int:
        return self.cleaned_data.get("min_frequency") or 2

    def clean_min_range(self) -> int:
        return self.cleaned_data.get("min_range") or 1

    def clean_pos(self) -> str:
        return self.cleaned_data.get("pos", "").strip()

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "log_dice"

    def clean_page_size(self) -> int:
        return int(self.cleaned_data.get("page_size") or 50)

    def clean_page(self) -> int:
        return self.cleaned_data.get("page") or 1


class ConcordancePlotForm(LanguageForm):
    q = forms.CharField(
        label="Search term",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    overlay_q = forms.CharField(
        label="Overlay term",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off", "placeholder": "可选对比词"}
        ),
    )
    sort_by = forms.ChoiceField(
        label="Sort by",
        choices=(
            ("doc_id", "DocID"),
            ("filename", "DocPath"),
            ("tokens", "DocTokens"),
            ("frequency", "Frequency"),
            ("normalized_frequency", "NormFrequency"),
            ("dispersion", "Dispersion"),
        ),
        initial="doc_id",
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    invert_order = forms.BooleanField(
        label="Invert order",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    show_zero_hits = forms.BooleanField(
        label="显示零命中文档",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    bin_count = forms.IntegerField(
        label="Dispersion bins",
        min_value=10,
        max_value=200,
        initial=100,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    def clean(self) -> dict:
        cleaned = super().clean()
        query = " ".join(cleaned.get("q", "").split())
        cleaned["q"] = query
        overlay_query = " ".join(cleaned.get("overlay_q", "").split())
        cleaned["overlay_q"] = overlay_query
        if query and not self.errors:
            try:
                detected_language, _ = query_terms(query)
            except ValueError as exc:
                raise forms.ValidationError(str(exc), code="invalid_query") from exc
            if detected_language != cleaned.get("language"):
                raise forms.ValidationError(
                    "检索词语言与所选语言不一致。",
                    code="language_mismatch",
                )
        if overlay_query and not self.errors:
            try:
                overlay_language, _ = query_terms(overlay_query)
            except ValueError as exc:
                raise forms.ValidationError(str(exc), code="invalid_overlay_query") from exc
            if overlay_language != cleaned.get("language"):
                raise forms.ValidationError(
                    "叠加检索词语言与所选语言不一致。",
                    code="overlay_language_mismatch",
                )
        return cleaned

    def clean_sort_by(self) -> str:
        return self.cleaned_data.get("sort_by") or "doc_id"

    def clean_bin_count(self) -> int:
        return self.cleaned_data.get("bin_count") or 100
