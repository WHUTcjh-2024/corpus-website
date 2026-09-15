# 正式教师语料导入

老师提供的语料目录通过专用管理命令登记为四个独立集合：人工对齐双语语料、原文候选双语语料、中文单语语料和英文单语语料。空文件等隔离项只保留在清单中，不进入检索索引。

## 导入前提

- 已执行数据库迁移；
- `DATA_ROOT` 指向持久化数据目录；
- `--source-root` 指向老师交付的正式语料根目录；
- 源目录在导入期间保持只读且路径稳定；
- 正式语料文件不提交到 Git 仓库。

## 执行命令

仅扫描、生成清单并登记数据库：

```powershell
python backend/manage.py import_formal_teacher_corpus `
  --source-root "D:\Desktop\陈俊宏-实验语料库" `
  --access-level advanced
```

登记后同步构建四个集合的检索索引：

```powershell
python backend/manage.py import_formal_teacher_corpus `
  --source-root "D:\Desktop\陈俊宏-实验语料库" `
  --access-level advanced `
  --process
```

命令会在 `DATA_ROOT/manifests/formal_teacher_corpus/` 写出 CSV 和 JSON 清单。重复执行会更新固定的正式语料记录、移除清单中已不存在的文件，并原子替换成功构建的索引，不会创建重复集合。

## 验收

导入完成后，对命令输出的四个语料库 ID 逐一执行：

```powershell
python backend/manage.py validate_corpus_indexes `
  --corpus-id <CORPUS_ID_1> `
  --corpus-id <CORPUS_ID_2> `
  --corpus-id <CORPUS_ID_3> `
  --corpus-id <CORPUS_ID_4>
```

验收同时检查索引文件健康状态、数据库与 SQLite 词元数一致性、KWIC 词频、词表统计以及双语预览。任何一项不一致都会以非零退出码结束。
