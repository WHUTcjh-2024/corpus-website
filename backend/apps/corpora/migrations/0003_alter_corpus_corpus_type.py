from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("corpora", "0002_corpusfile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="corpus",
            name="corpus_type",
            field=models.CharField(
                choices=[
                    ("raw_zh", "中文原文"),
                    ("raw_en", "英文原文"),
                    ("aligned_tsv", "中英对齐 TSV"),
                    ("paired_raw_zh_en", "中英原文配对"),
                    ("paired_tagged_zh_en", "中英人工对齐标注语料"),
                    ("tagged_zh", "中文词性标注"),
                    ("tagged_en", "英文词性标注"),
                    ("xml_like", "类 XML 结构"),
                    ("unknown", "待确认"),
                ],
                max_length=30,
                verbose_name="语料类型",
            ),
        ),
        migrations.AlterField(
            model_name="corpusfile",
            name="detected_type",
            field=models.CharField(
                choices=[
                    ("raw_zh", "中文原文"),
                    ("raw_en", "英文原文"),
                    ("aligned_tsv", "中英对齐 TSV"),
                    ("paired_raw_zh_en", "中英原文配对"),
                    ("paired_tagged_zh_en", "中英人工对齐标注语料"),
                    ("tagged_zh", "中文词性标注"),
                    ("tagged_en", "英文词性标注"),
                    ("xml_like", "类 XML 结构"),
                    ("unknown", "待确认"),
                ],
                max_length=30,
                verbose_name="检测类型",
            ),
        ),
    ]
