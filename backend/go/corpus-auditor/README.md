# 平行语料审计器

该程序是 Django 处理链路调用的离线执行器，不提供 HTTP 接口，也不直接访问业务数据库。

输入为处理链路生成的 `parallel_pairs.jsonl`；输出为版本化的 `quality_report.json` 与可复核的 `anomalies.jsonl`。它检查空侧、重复句对、同一源文首次出现不同译文、低/非法置信度，以及中英长度比例异常。输出文件通过临时文件加原子替换发布，避免页面读取到半成品。

```powershell
go test ./...
go build -o bin/corpus-auditor.exe ./cmd/corpus-auditor
.\bin\corpus-auditor.exe --input sample.jsonl --report quality_report.json --anomalies anomalies.jsonl
```

`--max-anomalies` 仅限制明细文件行数；汇总中的计数仍覆盖全部输入。
