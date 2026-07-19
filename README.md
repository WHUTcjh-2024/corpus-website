# KWIC 语料检索核心实现

这是一个在线语料库平台里的核心代码节选，只保留了 KWIC 检索、CQP 风格查询和索引生成相关实现。

这个仓库不是完整网站，也不能直接部署运行。它只是用来展示后端检索设计和工程实现能力。

## 包含内容

- KWIC 检索引擎
- 中英文 Token 归一化
- 短语检索、分页、上下文窗口
- L1/L2/L3/R1/R2/R3 排序
- 安全的 CQP 风格查询子集
- 参数化 SQL，避免查询注入
- SQLite 检索索引生成
- JSONL 加工产物写入
- 索引目录原子发布

## 代码位置

```text
backend/apps/search/
  kwic.py           KWIC 检索主逻辑
  query_parser.py   CQP 风格查询解析
  query_engine.py   复杂查询执行
  filters.py        查询条件到 SQL 的安全编译
  forms.py          查询参数校验

backend/apps/processing/
  artifacts.py      SQLite / JSONL 索引与产物生成
  text.py           分句、分词、归一化
  contracts.py      加工数据结构定义

backend/apps/corpus_intake/
  classifiers.py    文本编码识别与语料类型判断
```

## 说明

完整项目包含前端、权限、后台任务、部署配置和语料数据。这里都没有提交。

我只保留了最能体现检索能力的核心文件，方便别人快速看代码，而不是直接复制整个网站。
