from __future__ import annotations

from django import forms

from .models import Corpus
from .services import PersonalCorpusData, create_personal_corpus


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
