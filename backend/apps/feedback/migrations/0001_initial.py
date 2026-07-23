from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FeedbackTicket",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=160, verbose_name="标题")),
                ("category", models.CharField(choices=[("bug", "问题报告"), ("data", "数据异常"), ("feature", "功能建议"), ("account", "账号权限"), ("other", "其他")], default="bug", max_length=20, verbose_name="类型")),
                ("severity", models.CharField(choices=[("low", "一般"), ("medium", "影响使用"), ("high", "严重阻塞")], default="medium", max_length=20, verbose_name="影响程度")),
                ("status", models.CharField(choices=[("open", "待处理"), ("triaged", "已确认"), ("in_progress", "处理中"), ("resolved", "已解决"), ("closed", "已关闭")], db_index=True, default="open", max_length=20, verbose_name="状态")),
                ("page_url", models.CharField(blank=True, max_length=500, verbose_name="相关页面")),
                ("contact_email", models.EmailField(blank=True, max_length=254, verbose_name="联系邮箱")),
                ("description", models.TextField(verbose_name="详细说明")),
                ("admin_note", models.TextField(blank=True, verbose_name="处理备注")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="解决时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="提交时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feedback_tickets", to=settings.AUTH_USER_MODEL, verbose_name="提交用户")),
            ],
            options={
                "verbose_name": "反馈问题",
                "verbose_name_plural": "反馈问题",
                "ordering": ["-created_at"],
            },
        ),
    ]
