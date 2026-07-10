# 阶段 2：用户、权限与审核

## 范围

本阶段只实现 `accounts` 模块，不实现语料检索、上传或导出。

已实现：

- 基于 Django 内置认证用户的 `UserProfile` 申请与权限资料。
- `test`、`junior`、`middle`、`advanced`、`admin` 五种角色。
- `pending`、`approved`、`rejected`、`disabled` 四种状态。
- 用户申请、审核状态登录限制和工作台后端权限校验。
- Django Admin 审核、拒绝、调整等级和停用操作。
- 可重复执行的 `seed_accounts` 管理命令。

## 设计边界

认证继续使用 Django 内置用户表，申请字段和角色状态保存在 `accounts_userprofile`。这样不会破坏阶段 0 已执行的 Django 认证迁移，并为后续语料库所有者外键保留稳定接口。

业务状态变更集中在 `apps/accounts/services.py`，访问范围集中在 `apps/accounts/permissions.py`，页面不直接实现权限规则。

## 测试

覆盖申请字段、重复邮箱、待审核登录限制、审核通过、停用账号、重新启用、test_user demo 范围、普通用户禁止进入管理后台、缺失 profile 拒绝访问以及 seed 幂等性。

```powershell
cd backend
.\.venv\Scripts\pytest apps\accounts\tests.py
```

## 人工验收

1. 运行 `seed_accounts` 创建 `test_user` 和 `admin`。
2. 提交普通用户申请，确认状态为待审核且不能登录工作台。
3. 管理员在 Django Admin 中审核通过并调整角色。
4. 确认用户可以登录；再停用该用户，确认无法登录。
5. 使用普通用户访问 `/admin/`，确认不能进入管理后台。
