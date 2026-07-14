from __future__ import annotations

from django import forms

from .models import Corpus, CorpusLanguage, CorpusType
from .services import (
    PersonalCorpusData,
    UploadedCorpusData,
    create_personal_corpus,
    create_uploaded_parallel_corpus,
    create_uploaded_corpus,
    upload_limits_for,
)


class PersonalCorpusForm(forms.ModelForm):
    class Meta:
        model = Corpus
        fields = ("name", "corpus_type", "language", "description")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def save(self, commit: bool = True) -> Corpus:
        if not commit:
            raise ValueError("PersonalCorpusForm does not support commit=False.")
        if not self.is_valid():
            raise ValueError("Cannot save an invalid personal corpus form.")
        return create_personal_corpus(
            user=self.user,
            data=PersonalCorpusData(
                name=self.cleaned_data["name"],
                corpus_type=self.cleaned_data["corpus_type"],
                language=self.cleaned_data["language"],
                description=self.cleaned_data["description"],
            ),
        )


class CorpusUploadForm(forms.Form):
    MODE_MONOLINGUAL = "monolingual"
    MODE_PAIRED_RAW = "paired_raw"
    MODE_PAIRED_TAGGED = "paired_tagged"

    name = forms.CharField(label="语料库名称", max_length=200)
    upload_mode = forms.ChoiceField(
        label="语料类型",
        choices=(
            (MODE_MONOLINGUAL, "单语原始 TXT（AntConc）"),
            (MODE_PAIRED_RAW, "中英段落人工对齐 TXT（ParaConc）"),
            (MODE_PAIRED_TAGGED, "中英编号/POS 人工对齐 TXT（ParaConc + AntConc）"),
        ),
        initial=MODE_MONOLINGUAL,
        required=False,
    )
    language = forms.ChoiceField(
        label="文本语言",
        choices=((CorpusLanguage.ZH, "中文"), (CorpusLanguage.EN, "英文")),
        required=False,
    )
    description = forms.CharField(
        label="说明",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    source_file = forms.FileField(
        label="单语 TXT 文件",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".txt,text/plain"}),
    )
    zh_file = forms.FileField(
        label="中文 TXT 文件",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".txt,text/plain"}),
    )
    en_file = forms.FileField(
        label="英文 TXT 文件",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".txt,text/plain"}),
    )

    def __init__(self, *args, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                "class",
                "form-select" if isinstance(field.widget, forms.Select) else "form-control",
            )

    def _validate_file(self, uploaded_file):
        if uploaded_file is None:
            return None
        if not str(uploaded_file.name).lower().endswith(".txt"):
            raise forms.ValidationError("当前仅支持 .txt 文本语料。", code="invalid_extension")
        limit = upload_limits_for(self.user).max_file_bytes
        if uploaded_file.size <= 0:
            raise forms.ValidationError("不能上传空文件。", code="empty")
        if uploaded_file.size > limit:
            raise forms.ValidationError(
                f"单个文件不能超过 {limit // (1024 * 1024)} MB。",
                code="file_too_large",
            )
        return uploaded_file

    def clean_source_file(self):
        return self._validate_file(self.cleaned_data.get("source_file"))

    def clean_zh_file(self):
        return self._validate_file(self.cleaned_data.get("zh_file"))

    def clean_en_file(self):
        return self._validate_file(self.cleaned_data.get("en_file"))

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("upload_mode") or self.MODE_MONOLINGUAL
        if mode == self.MODE_MONOLINGUAL:
            if not cleaned.get("language"):
                self.add_error("language", "请选择单语文本语言。")
            if not cleaned.get("source_file"):
                self.add_error("source_file", "请选择单语 TXT 文件。")
        else:
            if not cleaned.get("zh_file"):
                self.add_error("zh_file", "请选择中文 TXT 文件。")
            if not cleaned.get("en_file"):
                self.add_error("en_file", "请选择英文 TXT 文件。")
        return cleaned

    def save(self):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid upload form.")
        mode = self.cleaned_data.get("upload_mode") or self.MODE_MONOLINGUAL
        data = UploadedCorpusData(
            name=self.cleaned_data["name"],
            language=(
                self.cleaned_data.get("language")
                if mode == self.MODE_MONOLINGUAL
                else CorpusLanguage.ZH_EN
            ),
            description=self.cleaned_data["description"],
        )
        if mode == self.MODE_MONOLINGUAL:
            return create_uploaded_corpus(
                user=self.user,
                data=data,
                uploaded_file=self.cleaned_data["source_file"],
            )
        corpus_type = (
            CorpusType.PAIRED_RAW_ZH_EN
            if mode == self.MODE_PAIRED_RAW
            else CorpusType.PAIRED_TAGGED_ZH_EN
        )
        return create_uploaded_parallel_corpus(
            user=self.user,
            data=data,
            corpus_type=corpus_type,
            zh_file=self.cleaned_data["zh_file"],
            en_file=self.cleaned_data["en_file"],
        )
