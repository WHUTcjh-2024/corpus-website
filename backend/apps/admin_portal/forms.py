from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusType,
)
from apps.corpora.services import (
    ManagedUploadedCorpusData,
    create_managed_uploaded_corpus,
    replace_corpus_access_grants,
)
from apps.feedback.models import FeedbackStatus, FeedbackTicket

from .models import Announcement, AnnouncementAudience
from .services import replace_announcement_recipients


def approved_recipient_queryset():
    user_model = get_user_model()
    return (
        user_model.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(account_profile__status=ApplicationStatus.APPROVED))
        .select_related("account_profile")
        .distinct()
        .order_by("username")
    )


class RecipientMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user) -> str:
        try:
            profile = user.account_profile
        except UserProfile.DoesNotExist:
            return f"{user.username}（管理员）"
        return f"{profile.full_name}（{user.username} · {profile.get_role_display()}）"


class ManagedCorpusUploadForm(forms.Form):
    class PublishScope(models.TextChoices):
        SELECTED = "selected", "仅指定用户"
        APPROVED = "approved", "全部正式用户"
        DEMO = "demo", "演示范围（含测试账号）"

    class UploadMode(models.TextChoices):
        MONOLINGUAL = "monolingual", "单语文本"
        PARALLEL = "parallel", "中英双语配对文本"

    name = forms.CharField(label="语料库名称", max_length=200)
    upload_mode = forms.ChoiceField(label="语料形式", choices=UploadMode.choices)
    language = forms.ChoiceField(
        label="单语语言",
        choices=[(CorpusLanguage.ZH, "中文"), (CorpusLanguage.EN, "英文")],
        required=False,
    )
    publish_scope = forms.ChoiceField(label="发布范围", choices=PublishScope.choices)
    recipients = RecipientMultipleChoiceField(
        label="可见用户",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
        help_text="仅指定用户模式需要至少选择一名已审核用户。",
    )
    description = forms.CharField(
        label="说明",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    source_file = forms.FileField(label="单语 TXT 文件", required=False)
    zh_file = forms.FileField(label="中文 TXT 文件", required=False)
    en_file = forms.FileField(label="英文 TXT 文件", required=False)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = approved_recipient_queryset()
        for name, field in self.fields.items():
            if name == "recipients":
                continue
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css_class)

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        mode = cleaned.get("upload_mode")
        scope = cleaned.get("publish_scope")
        if scope == self.PublishScope.SELECTED and not cleaned.get("recipients"):
            self.add_error("recipients", "请至少选择一名可见用户。")
        if mode == self.UploadMode.MONOLINGUAL:
            if not cleaned.get("language"):
                self.add_error("language", "请选择单语语料的语言。")
            if not cleaned.get("source_file"):
                self.add_error("source_file", "请选择单语 TXT 文件。")
        elif mode == self.UploadMode.PARALLEL:
            if not cleaned.get("zh_file"):
                self.add_error("zh_file", "请选择中文 TXT 文件。")
            if not cleaned.get("en_file"):
                self.add_error("en_file", "请选择英文 TXT 文件。")
        return cleaned

    def save(self, *, actor):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid managed corpus upload form.")
        mode = self.cleaned_data["upload_mode"]
        scope = self.cleaned_data["publish_scope"]
        if scope == self.PublishScope.SELECTED:
            source_type = CorpusSourceType.TEACHER
            access_level = CorpusAccessLevel.PRIVATE
            recipients = self.cleaned_data["recipients"]
        elif scope == self.PublishScope.APPROVED:
            source_type = CorpusSourceType.TEACHER
            access_level = CorpusAccessLevel.JUNIOR
            recipients = ()
        else:
            source_type = CorpusSourceType.DEMO
            access_level = CorpusAccessLevel.DEMO
            recipients = ()

        if mode == self.UploadMode.PARALLEL:
            corpus_type = CorpusType.PAIRED_RAW_ZH_EN
            language = CorpusLanguage.ZH_EN
            files = (
                (self.cleaned_data["zh_file"], CorpusLanguage.ZH),
                (self.cleaned_data["en_file"], CorpusLanguage.EN),
            )
        else:
            language = self.cleaned_data["language"]
            corpus_type = CorpusType.RAW_ZH if language == CorpusLanguage.ZH else CorpusType.RAW_EN
            files = ((self.cleaned_data["source_file"], language),)

        return create_managed_uploaded_corpus(
            actor=actor,
            data=ManagedUploadedCorpusData(
                name=self.cleaned_data["name"],
                corpus_type=corpus_type,
                language=language,
                source_type=source_type,
                access_level=access_level,
                description=self.cleaned_data["description"],
            ),
            files=files,
            recipients=recipients,
        )


class CorpusVisibilityForm(forms.Form):
    class Scope(models.TextChoices):
        SELECTED = "selected", "仅指定用户"
        APPROVED = "approved", "全部正式用户"
        DEMO = "demo", "演示范围（含测试账号）"

    publish_scope = forms.ChoiceField(label="发布范围", choices=Scope.choices)
    recipients = RecipientMultipleChoiceField(
        label="可见用户",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 10}),
    )

    def __init__(self, *args, corpus: Corpus, **kwargs) -> None:
        self.corpus = corpus
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = approved_recipient_queryset()
        if not self.is_bound:
            if corpus.source_type == CorpusSourceType.DEMO:
                scope = self.Scope.DEMO
            elif corpus.access_level == CorpusAccessLevel.PRIVATE:
                scope = self.Scope.SELECTED
            else:
                scope = self.Scope.APPROVED
            self.initial.update(
                {
                    "publish_scope": scope,
                    "recipients": corpus.access_grants.values_list("user_id", flat=True),
                }
            )

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if cleaned.get("publish_scope") == self.Scope.SELECTED and not cleaned.get("recipients"):
            self.add_error("recipients", "指定用户模式至少需要一名已审核用户。")
        return cleaned

    def save(self, *, actor) -> Corpus:
        if not self.is_valid():
            raise ValueError("Cannot save an invalid corpus visibility form.")
        corpus = Corpus.objects.get(pk=self.corpus.pk)
        scope = self.cleaned_data["publish_scope"]
        if scope == self.Scope.SELECTED:
            corpus.source_type = CorpusSourceType.TEACHER
            corpus.access_level = CorpusAccessLevel.PRIVATE
            corpus.full_clean()
            corpus.save(update_fields=["source_type", "access_level", "updated_at"])
            replace_corpus_access_grants(
                corpus=corpus,
                recipients=self.cleaned_data["recipients"],
                granted_by=actor,
            )
        else:
            corpus.source_type = (
                CorpusSourceType.DEMO if scope == self.Scope.DEMO else CorpusSourceType.TEACHER
            )
            corpus.access_level = (
                CorpusAccessLevel.DEMO if scope == self.Scope.DEMO else CorpusAccessLevel.JUNIOR
            )
            corpus.full_clean()
            corpus.save(update_fields=["source_type", "access_level", "updated_at"])
            corpus.access_grants.all().delete()
        return corpus


class AnnouncementForm(forms.ModelForm):
    recipients = RecipientMultipleChoiceField(
        label="接收用户",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    class Meta:
        model = Announcement
        fields = ("title", "body", "audience", "recipients", "starts_at", "ends_at", "is_published")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 7}),
            "audience": forms.Select(attrs={"class": "form-select"}),
            "starts_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "is_published": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = approved_recipient_queryset()
        if self.instance.pk and not self.is_bound:
            self.initial["recipients"] = self.instance.recipients.values_list("pk", flat=True)

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if cleaned.get("audience") == AnnouncementAudience.SELECTED and not cleaned.get("recipients"):
            self.add_error("recipients", "指定用户公告至少需要一名接收用户。")
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "结束展示时间必须晚于开始展示时间。")
        return cleaned

    def save(self, *, actor, commit: bool = True) -> Announcement:
        announcement = super().save(commit=False)
        if announcement.created_by_id is None:
            announcement.created_by = actor
        if announcement.is_published and announcement.published_at is None:
            announcement.published_at = timezone.now()
            announcement.published_by = actor
        if commit:
            announcement.full_clean()
            announcement.save()
            recipients = self.cleaned_data["recipients"] if announcement.audience == AnnouncementAudience.SELECTED else ()
            replace_announcement_recipients(announcement=announcement, recipients=recipients)
        return announcement


class AccountReviewForm(forms.Form):
    role = forms.ChoiceField(label="账号等级", choices=UserRole.choices, widget=forms.Select(attrs={"class": "form-select"}))
    status = forms.ChoiceField(
        label="审核结果",
        choices=[
            (ApplicationStatus.APPROVED, "审核通过"),
            (ApplicationStatus.REJECTED, "拒绝申请"),
            (ApplicationStatus.DISABLED, "停用账号"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class FeedbackResolutionForm(forms.ModelForm):
    class Meta:
        model = FeedbackTicket
        fields = ("status", "admin_note")
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "admin_note": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }

    def save(self, commit: bool = True) -> FeedbackTicket:
        ticket = super().save(commit=False)
        ticket.resolved_at = (
            timezone.now()
            if ticket.status in {FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED}
            else None
        )
        if commit:
            ticket.save()
        return ticket
