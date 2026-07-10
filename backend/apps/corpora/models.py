from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CorpusSourceType(models.TextChoices):
    TEACHER = "teacher", "教师语料"
    DEMO = "demo", "演示语料"
    USER = "user", "用户自建语料"


class CorpusType(models.TextChoices):
    RAW_ZH = "raw_zh", "中文原文"
    RAW_EN = "raw_en", "英文原文"
    ALIGNED_TSV = "aligned_tsv", "中英对齐 TSV"
    PAIRED_RAW_ZH_EN = "paired_raw_zh_en", "中英原文配对"
    TAGGED_ZH = "tagged_zh", "中文词性标注"
    TAGGED_EN = "tagged_en", "英文词性标注"
    XML_LIKE = "xml_like", "类 XML 结构"
    UNKNOWN = "unknown", "待确认"


class CorpusLanguage(models.TextChoices):
    ZH = "zh", "中文"
    EN = "en", "英文"
    ZH_EN = "zh_en", "中英双语"
    UNKNOWN = "unknown", "待确认"


class CorpusAccessLevel(models.TextChoices):
    DEMO = "demo", "演示范围"
    JUNIOR = "junior", "初级及以上"
    MIDDLE = "middle", "中级及以上"
    ADVANCED = "advanced", "高级及以上"
    PRIVATE = "private", "仅所有者"


class CorpusStatus(models.TextChoices):
    CREATED = "created", "已登记"
    PENDING_PROCESSING = "pending_processing", "等待加工"
    PROCESSING = "processing", "加工中"
    READY = "ready", "可用"
    FAILED = "failed", "失败"
    DISABLED = "disabled", "已停用"


class CorpusFileStatus(models.TextChoices):
    PENDING = "pending", "等待加工"
    PROCESSING = "processing", "加工中"
    READY = "ready", "已加工"
    FAILED = "failed", "加工失败"
    DISABLED = "disabled", "已停用"


class Corpus(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("语料库名称", max_length=200)
    source_type = models.CharField(
        "来源类型",
        max_length=20,
        choices=CorpusSourceType.choices,
        db_index=True,
    )
    corpus_type = models.CharField(
        "语料类型",
        max_length=30,
        choices=CorpusType.choices,
    )
    language = models.CharField(
        "语言",
        max_length=20,
        choices=CorpusLanguage.choices,
        default=CorpusLanguage.UNKNOWN,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_corpora",
        null=True,
        blank=True,
        verbose_name="所有者",
    )
    access_level = models.CharField(
        "访问等级",
        max_length=20,
        choices=CorpusAccessLevel.choices,
        default=CorpusAccessLevel.JUNIOR,
        db_index=True,
    )
    status = models.CharField(
        "状态",
        max_length=30,
        choices=CorpusStatus.choices,
        default=CorpusStatus.CREATED,
        db_index=True,
    )
    stage = models.CharField("当前阶段", max_length=50, default="registered")
    description = models.TextField("说明", blank=True)
    manifest_file_id = models.CharField(
        "manifest 文件 ID",
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )
    manifest_relative_path = models.CharField(
        "manifest 原始相对路径",
        max_length=1000,
        blank=True,
    )
    manifest_size_bytes = models.PositiveBigIntegerField(
        "manifest 文件大小",
        default=0,
    )
    manifest_encoding = models.CharField("manifest 编码", max_length=50, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        verbose_name = "语料库"
        verbose_name_plural = "语料库"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(source_type=CorpusSourceType.USER, owner__isnull=False)
                    | models.Q(
                        source_type__in=[CorpusSourceType.TEACHER, CorpusSourceType.DEMO],
                        owner__isnull=True,
                    )
                ),
                name="corpus_owner_matches_source_type",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.source_type == CorpusSourceType.USER:
            if self.owner_id is None:
                raise ValidationError({"owner": "用户自建语料必须指定所有者。"})
            if self.access_level != CorpusAccessLevel.PRIVATE:
                raise ValidationError({"access_level": "用户自建语料必须设为仅所有者。"})
        elif self.owner_id is not None:
            raise ValidationError({"owner": "教师语料和演示语料不能指定个人所有者。"})


class CorpusDocumentation(models.Model):
    corpus = models.OneToOneField(
        Corpus,
        on_delete=models.CASCADE,
        related_name="documentation",
        primary_key=True,
        verbose_name="语料库",
    )
    file_count = models.PositiveIntegerField("文件数", default=0)
    document_count = models.PositiveIntegerField("文档数", default=0)
    paragraph_count = models.PositiveBigIntegerField("段落数", default=0)
    sentence_count = models.PositiveBigIntegerField("句子数", default=0)
    token_count = models.PositiveBigIntegerField("Token 数", default=0)
    type_count = models.PositiveBigIntegerField("Type 数", default=0)
    segmentation_tool = models.CharField("分词工具", max_length=200, blank=True)
    processing_notes = models.TextField("加工说明", blank=True)
    copyright_notice = models.TextField("版权说明", blank=True)
    corpus_created_at = models.DateField("语料形成日期", null=True, blank=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "Corpus Documentation"
        verbose_name_plural = "Corpus Documentation"

    def __str__(self) -> str:
        return f"Documentation: {self.corpus.name}"


class CorpusFile(models.Model):
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="语料库",
    )
    original_filename = models.CharField("原始文件名", max_length=500)
    stored_path = models.CharField("存储路径", max_length=1500)
    manifest_file_id = models.CharField("manifest 文件 ID", max_length=64, blank=True)
    detected_type = models.CharField(
        "检测类型",
        max_length=30,
        choices=CorpusType.choices,
    )
    language = models.CharField(
        "语言",
        max_length=20,
        choices=CorpusLanguage.choices,
        default=CorpusLanguage.UNKNOWN,
    )
    size_bytes = models.PositiveBigIntegerField("文件大小", default=0)
    encoding = models.CharField("编码", max_length=50, blank=True)
    checksum_sha256 = models.CharField("SHA-256", max_length=64, blank=True)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=CorpusFileStatus.choices,
        default=CorpusFileStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField("错误信息", blank=True)
    created_at = models.DateTimeField("登记时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["created_at", "original_filename"]
        verbose_name = "语料文件"
        verbose_name_plural = "语料文件"
        constraints = [
            models.UniqueConstraint(
                fields=["corpus", "stored_path"],
                name="unique_corpus_stored_path",
            )
        ]

    def __str__(self) -> str:
        return f"{self.corpus.name}: {self.original_filename}"
