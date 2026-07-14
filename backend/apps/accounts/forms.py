from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import UploadQuotaRequest, UserProfile, UserRole
from .permissions import AccessScope, workspace_access_scope
from .services import ApplicationData, submit_application, submit_quota_request


class AccountApplicationForm(forms.Form):
    username = forms.CharField(label="用户名", min_length=3, max_length=150)
    full_name = forms.CharField(label="姓名", max_length=100)
    organization = forms.CharField(label="单位", max_length=200)
    email = forms.EmailField(label="邮箱")
    requested_role = forms.ChoiceField(
        label="申请等级",
        choices=[
            (UserRole.JUNIOR, UserRole.JUNIOR.label),
            (UserRole.MIDDLE, UserRole.MIDDLE.label),
            (UserRole.ADVANCED, UserRole.ADVANCED.label),
        ],
    )
    use_purpose = forms.CharField(label="使用目的", max_length=200)
    application_reason = forms.CharField(
        label="申请理由",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    password1 = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="确认密码", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_username(self) -> str:
        username = self.cleaned_data["username"].strip()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("该用户名已被使用。", code="duplicate_username")
        return username

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if UserProfile.objects.filter(email__iexact=email).exists():
            raise ValidationError("该邮箱已提交过申请。", code="duplicate_email")
        return email

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "两次输入的密码不一致。")
        if password1:
            user = get_user_model()(
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email", ""),
            )
            try:
                validate_password(password1, user=user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned_data

    def save(self) -> UserProfile:
        if not self.is_valid():
            raise ValueError("Cannot save an invalid account application form.")
        return submit_application(
            ApplicationData(
                username=self.cleaned_data["username"],
                password=self.cleaned_data["password1"],
                full_name=self.cleaned_data["full_name"],
                organization=self.cleaned_data["organization"],
                email=self.cleaned_data["email"],
                requested_role=self.cleaned_data["requested_role"],
                use_purpose=self.cleaned_data["use_purpose"],
                application_reason=self.cleaned_data["application_reason"],
            )
        )


class ApprovedUserAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "not_approved": "账号尚未审核通过或已被停用。",
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if workspace_access_scope(user) == AccessScope.NONE:
            raise ValidationError(
                self.error_messages["not_approved"],
                code="not_approved",
            )


class UploadQuotaRequestForm(forms.Form):
    requested_max_file_mb = forms.IntegerField(
        label="申请单文件上限（MB）",
        min_value=1,
        max_value=10_240,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    requested_total_mb = forms.IntegerField(
        label="申请账号总额（MB）",
        min_value=1,
        max_value=10_240,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    reason = forms.CharField(
        label="扩容理由",
        max_length=2000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5}),
    )

    def __init__(self, *args, user, current_max_file_bytes: int, current_total_bytes: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.current_total_bytes = current_total_bytes
        self.fields["requested_max_file_mb"].initial = max(
            1, current_max_file_bytes // (1024 * 1024)
        )
        self.fields["requested_total_mb"].initial = max(
            1, current_total_bytes // (1024 * 1024)
        )

    def clean(self) -> dict:
        cleaned = super().clean()
        max_file_mb = cleaned.get("requested_max_file_mb")
        total_mb = cleaned.get("requested_total_mb")
        if max_file_mb and total_mb and max_file_mb > total_mb:
            self.add_error("requested_max_file_mb", "单文件上限不能超过账号总额。")
        if total_mb and total_mb * 1024 * 1024 <= self.current_total_bytes:
            self.add_error("requested_total_mb", "申请总额必须高于当前配额。")
        return cleaned

    def save(self) -> UploadQuotaRequest:
        if not self.is_valid():
            raise ValueError("Cannot save an invalid quota request form.")
        return submit_quota_request(
            user=self.user,
            requested_max_file_bytes=self.cleaned_data["requested_max_file_mb"] * 1024 * 1024,
            requested_total_bytes=self.cleaned_data["requested_total_mb"] * 1024 * 1024,
            reason=self.cleaned_data["reason"],
        )
