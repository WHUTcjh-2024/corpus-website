# 测试证据

本目录只保留可复核、可再生成的合同验收证据，不存放临时日志、截图或本地环境容量结论。

## 文件说明

- `正式语料导入验证结果.json`：正式语料导入和索引校验结果。
- `正式语料库准确度测试结果.json`：准确度、性能和预期值比对的结构化结果。
- `平台准确度测试明细.csv`：逐项准确度明细。
- `四十组平行语料结构测试明细.csv`：平行语料结构明细。
- `正式语料库准确度测试复核.ipynb`：可重复执行的人工复核入口。
- `报告可访问性检查.json`：交付报告的可访问性检查摘要。
- `staging-2c4g-2026-09-19.json`：2C4G 容器化 staging 压测结果；环境、阈值和限制见
  [`../STAGING_2C4G_VALIDATION.md`](../STAGING_2C4G_VALIDATION.md)。
- `figures/`：由报告生成脚本使用的汇总图。

## 再生成

在仓库根目录执行：

```powershell
backend/.venv/Scripts/python.exe scripts/run_formal_corpus_accuracy.py `
  --source-root <正式语料库目录> `
  --project-root . `
  --output-dir docs/test-evidence
backend/.venv/Scripts/python.exe scripts/build_accuracy_notebook.py
```

真实 2 核 4 GB staging 压测结果属于部署环境证据，由压测命令按时间生成，不提交访问令牌、主机信息或临时运行日志。
