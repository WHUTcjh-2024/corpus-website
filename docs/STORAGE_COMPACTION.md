# 语料索引容量

新加工语料把完整分词记录保存为 `processed/<id>/tokens.jsonl.gz`。旧语料的
`tokens.jsonl` 仍可被索引健康检查识别。SQLite 的 `tokens` 表只保存查询会用到
的字段；`ngrams` 使用 `WITHOUT ROWID`，并移除了没有查询使用的字符偏移索引。
KWIC、平行检索、词表和 2–5 元词组的查询接口保持不变。

已有语料可逐个建立隔离副本并对照查询结果：

```powershell
python backend/manage.py compact_corpus_storage <语料UUID> --output-root data/.storage-pilot
python scripts/verify_compact_corpora.py <语料UUID> --pilot-root data/.storage-pilot
python backend/manage.py publish_compact_corpus_storage <语料UUID> --pilot-root data/.storage-pilot
python backend/manage.py validate_corpus_indexes --corpus-id <语料UUID>
```

构建隔离副本需要额外磁盘空间；至少预留一份新索引及分词压缩包的容量。
发布命令使用同卷硬链接保留旧 SQLite，并保留旧 `tokens.jsonl`，供验收后回退。
只有线上索引验收通过后，才删除发布命令打印的旧 SQLite 备份和对应的
`tokens.jsonl`。最后可移除 `data/.storage-pilot` 中已发布的空目录。

容量继续随语料规模增长。部署时按 [部署说明](DEPLOYMENT.md) 监控 `data/`
剩余空间，并根据实际查询负载决定是否进一步缩减预计算词组索引。
