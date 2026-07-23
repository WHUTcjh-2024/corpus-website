from __future__ import annotations

from django import forms

from .models import FeedbackTicket


class FeedbackTicketForm(forms.ModelForm):
    class Meta:
        model = FeedbackTicket
        fields = [
            "title",
            "category",
            "severity",
            "page_url",
            "contact_email",
            "description",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "severity": forms.Select(attrs={"class": "form-select"}),
            "page_url": forms.TextInput(attrs={"class": "form-control"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 7}),
        }
