# 阶段 3：语料库管理与 Corpus Documentation

## 范围

本阶段只实现语料库登记、角色可见性和文档信息，不实现文件上传、语料加工或检索。

已实现：

- `Corpus` 结构化元数据模型及完整状态枚举。
- `CorpusDocumentation` 说明和统计占位模型。
- demo、教师、用户自建三种来源。
- junior、middle、advanced 分级可见性和个人语料所有者隔离。
- 语料库列表、个人元数据登记和 Documentation 页面。
- Django Admin 语料库维护。
- `register_manifest_corpus` 命令，从阶段 1 JSON manifest 登记教师或 demo 语料。

## 数据边界

PostgreSQL 只保存语料库元数据、manifest 引用和统计字段。命令不会复制或写入语料全文，个人登记页面也不接收文件。

## 测试

覆盖 test_user 仅 demo、junior 不可见 advanced、middle/advanced 等级继承、用户私有隔离、管理员全量可见、个人登记、test_user 禁止登记、Documentation 页面、manifest 幂等登记和状态枚举。

```powershell
cd backend
.\.venv\Scripts\pytest apps\corpora\tests.py
```

## 人工验收

1. 管理员创建 demo、junior、middle、advanced 等级语料库。
2. 分别登录 test_user、junior、middle、advanced，核对列表可见性。
3. 用户 A 和用户 B 分别登记个人条目，确认互相不可见。
4. 访问未授权 Documentation URL，确认返回 404。
5. 使用管理员确认可以看到全部语料，包括停用条目。
6. 从阶段 1 manifest 登记一条 demo 记录，核对类型、语言、相对路径、大小和编码。
