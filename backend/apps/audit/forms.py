from __future__ import annotations

from django import forms

from .models import SavedSearchKind


class SaveSearchForm(forms.Form):
    corpus_id = forms.UUIDField()
    kind = forms.ChoiceField(choices=SavedSearchKind.choices)
    name = forms.CharField(max_length=120, required=False)
    query_string = forms.CharField(max_length=5000, required=False)
