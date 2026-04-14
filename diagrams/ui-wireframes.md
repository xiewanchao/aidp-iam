# AIDP IAM 管理台 UI 线框图

> 版本：v1.0
> 日期：2026-04-14
> 工具：PlantUML Salt
> 粘贴到 https://www.plantuml.com/plantuml/uml/ 或本地 PlantUML 渲染器可直接预览

---

## 目录

1. [登录与身份切换](#1-登录与身份切换)
2. [平台控制台 (master-admin)](#2-平台控制台-master-admin)
3. [租户控制台 (tenant-admin)](#3-租户控制台-tenant-admin)
4. [应用控制台 (app-admin)](#4-应用控制台-app-admin)
5. [个人中心](#5-个人中心)

---

## 1 登录与身份切换

### 1.1 登录页

```plantuml
@startsalt
{
  { <b>AIDP 统一身份认证平台</b>
    .
    登录到租户:
    ^请选择租户^
    .
    { [使用账号密码登录]
    }
    .
    — 或 —
    .
    { [SSO 企业登录 (SAML/OIDC)]
      [GitHub]  [Google]  [企业微信]
    }
    .
    忘记密码? | 联系管理员
  }
}
@endsalt
```

### 1.2 登录后角色判定（流程，非页面）

```plantuml
@startsalt
{
  用户登录成功 → 解析 JWT.groups
  --
  * master-admins in groups → 跳转【平台控制台】
  * tenant-admins in groups → 跳转【租户控制台】
  * {app}-admins in groups  → 跳转【对应应用控制台】
  * 仅 all-users            → 跳转【个人中心】
}
@endsalt
```

---

## 2 平台控制台 (master-admin)

### 2.1 平台概览仪表盘

```plantuml
@startsalt
{+
  {* AIDP 平台 | 概览 | 租户 | 应用 | 路径规则 | 审计 | ^super-admin^ }
  --
  {# 关键指标
     | 租户数  | 用户数  | 应用数  | API QPS
     | <b>12</b> | <b>1,380</b> | <b>6</b>   | <b>2,430</b>
  }
  --
  {SI
    最近活动                                                | 系统健康
    ----                                                 | ----
    2026-04-14 10:12  tenant customer-b created          | Keycloak   ● 正常
    2026-04-14 09:45  app memory license disabled        | PostgreSQL ● 正常
    2026-04-14 09:30  master-admin login from 10.1.2.3   | OPA/OPAL   ● 正常
    2026-04-14 09:10  API key rotated for svc-crawler    | Gateway    ● 正常
  }
  --
  [查看全部审计日志]
}
@endsalt
```

### 2.2 租户列表

```plantuml
@startsalt
{+
  {* 平台 | <b>租户</b> | 应用 | 路径规则 | 审计 }
  --
  {
    搜索: "kw..." | 状态: ^全部^ | [重置] | .. | [+ 新建租户]
  }
  --
  {#
    | Realm     | 显示名       | 租户管理员       | 用户数 | 状态   | 创建时间   | 操作
    | aidp      | AIDP 默认    | tenant-admin    | 45    | 正常   | 2026-01-10 | 详情 | 冻结 | 删除
    | corp-a    | 客户 A       | admin@corp-a    | 230   | 正常   | 2026-02-15 | 详情 | 冻结 | 删除
    | corp-b    | 客户 B       | admin@corp-b    | 12    | 正常   | 2026-04-14 | 详情 | 冻结 | 删除
    | demo      | Demo 环境    | demo-admin      | 5     | 已冻结 | 2026-03-01 | 详情 | 激活 | 删除
  }
  --
  << 上一页  [1] [2] [3]  下一页 >>
}
@endsalt
```

### 2.3 新建租户（3 步向导）

```plantuml
@startsalt
{+
  {* 平台 | <b>租户</b> / 新建 }
  --
  {
    {/ 1. 基本信息 | 2. 管理员交付 | 3. 确认 }
  }
  --
  {
    Realm 名称:        "customer-c"           (用于 URL，小写+数字+短横线)
    显示名:            "客户 C 公司"
    租户 Logo:         [上传图片]             (可选)
    默认语言:          ^zh-CN^
    启用应用:          [X] knowledgebase
                       [X] memory
                       [ ] llm
                       [ ] fintech
  }
  --
  [取消]                                            [< 上一步]  [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* 平台 | <b>租户</b> / 新建 }
  --
  {/ 1. 基本信息 | <b>2. 管理员交付</b> | 3. 确认 }
  --
  {
    租户管理员交付方式:
    .
    (X) 邀请邮件 (推荐)
        管理员邮箱: "admin@customer-c.com"
        Keycloak 发送激活链接，用户点击后自设密码
    .
    ( ) 随机密码 + 首次强制改密
        用户名:     "admin"
        密码:       <创建后一次性展示>
        转交方式:   企业 IM / 加密文件 / 当面
    .
    ( ) 绑定外部 IdP (已配置)
        选择 IdP:   ^customer-c-okta^
  }
  --
  [取消]                                            [< 上一步]  [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* 平台 | <b>租户</b> / 新建 }
  --
  {/ 1. 基本信息 | 2. 管理员交付 | <b>3. 确认</b> }
  --
  {
    请确认以下信息:
    --
    | Realm     | customer-c
    | 显示名    | 客户 C 公司
    | 启用应用  | knowledgebase, memory
    | 管理员    | admin@customer-c.com (邀请邮件)
    --
    注意: 创建后将自动:
     * 创建 tenant-admins / all-users 组
     * 创建 data-agent client 并配置 JWT mapper
     * 向管理员邮箱发送激活链接
  }
  --
  [取消]                                            [< 上一步]  [✓ 创建租户]
}
@endsalt
```

### 2.4 租户详情

```plantuml
@startsalt
{+
  {* 平台 | <b>租户</b> / customer-a }
  --
  {
    {/ 基本信息 | 应用启用 | 管理员 | 用量 | 危险操作 }
  }
  --
  {
    Realm:        customer-a                    [复制]
    显示名:       客户 A                        [编辑]
    创建时间:     2026-02-15 14:30
    状态:         正常                          [冻结]
    --
    用户数:       230
    用户组数:     8
    API Key 数:   12
    --
    数据导出:     [下载用户列表 CSV]  [下载 ACL 快照]
  }
  --
  [返回列表]                                    [进入租户视角 >]
}
@endsalt
```

### 2.5 应用管理 - 列表

```plantuml
@startsalt
{+
  {* 平台 | 租户 | <b>应用</b> | 路径规则 | 审计 }
  --
  {
    搜索: "app 名..."                                  | [+ 注册应用]
  }
  --
  {#
    | App Name      | Path Prefix        | 显示名     | License | 使用租户 | 操作
    | knowledgebase | /knowledgebase/    | 知识库     | ✓ 启用  | 10      | 详情 | 下线
    | memory        | /memory/           | 记忆库     | ✓ 启用  | 8       | 详情 | 下线
    | llm           | /llm/              | LLM 路由   | ✗ 禁用  | 3       | 详情 | 启用
    | fintech       | /fintech/          | 金融分析   | ✓ 启用  | 2       | 详情 | 下线
  }
}
@endsalt
```

### 2.6 注册应用（4 个 Tab）

```plantuml
@startsalt
{+
  {* 平台 | <b>应用</b> / 注册 }
  --
  {/ 1. 基本信息 | 2. 资源模式 | 3. 资源动作 | 4. 路径规则预览 }
  --
  {
    App Name (唯一):    "llm"                     (小写+数字+短横线)
    Path Prefix:        "/llm/"                   (Gateway 路由前缀)
    显示名:             "LLM 路由中心"
    简介:               "统一 OpenAI 兼容 API..."
    初始启用:           [X] 注册后立即启用 License
  }
  --
  [取消]                                           [保存草稿]  [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* 平台 | <b>应用</b> / 注册 / llm }
  --
  {/ 1. 基本信息 | <b>2. 资源模式</b> | 3. 资源动作 | 4. 路径规则预览 }
  --
  {
    定义该应用对外暴露的资源类型，决定 ext_proc 如何自动同步 ACL:
  }
  --
  {#
    | Resource Prefix | Resource Type | id_source | id_field     | 操作
    | /v1/chats       | chat          | path      | id           | 编辑 | 删除
    | /v1/prompts     | prompt        | body      | data.id      | 编辑 | 删除
  }
  --
  {+ + 添加资源模式
    [测试提取 id]      URL: "/v1/chats/abc-001"  → 提取结果: abc-001 ✓
  }
  --
  [取消]                                           [< 上一步]  [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* 平台 | <b>应用</b> / 注册 / llm }
  --
  {/ 1. 基本信息 | 2. 资源模式 | <b>3. 资源动作</b> | 4. 路径规则预览 }
  --
  {
    非标准 RESTful API 的动作映射 (可选，为空则使用默认):
  }
  --
  {#
    | Prefix     | Method | Path Suffix  | Action   | Min Perm    | 操作
    | /v1/chats  | POST   | /send        | message  | contributor | 编辑 | 删除
    | /v1/chats  | POST   | /share       | share    | owner       | 编辑 | 删除
  }
  --
  {+ + 添加动作映射
  }
  --
  说明: 默认映射 GET→viewer, PUT/PATCH→contributor, DELETE→owner
  --
  [取消]                                           [< 上一步]  [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* 平台 | <b>应用</b> / 注册 / llm }
  --
  {/ 1. 基本信息 | 2. 资源模式 | 3. 资源动作 | <b>4. 路径规则预览</b> }
  --
  {
    为该应用添加路径级访问控制规则 (可选):
  }
  --
  {#
    | Path Pattern             | Required Group | 说明
    | /llm/v1/admin/*          | llm-admins     | 管理接口仅 LLM 管理员
    | /llm/v1/chats/*          | all-users      | 普通用户可访问
  }
  --
  {+ + 添加路径规则
  }
  --
  {SI
    实时 Rego 预览:
    --
    package http.authz
    --
    allow {
      "master-admins" in input.groups
    }
    allow {
      input.path == "/llm/v1/admin/*"
      "llm-admins" in input.groups
    }
    ...
  }
  --
  [取消]                                           [< 上一步]  [✓ 注册应用]
}
@endsalt
```

### 2.7 应用详情

```plantuml
@startsalt
{+
  {* 平台 | <b>应用</b> / knowledgebase }
  --
  {/ 基本信息 | 资源模式 | 资源动作 | 路径规则 | 在租户启用情况 }
  --
  {
    App Name:       knowledgebase
    Path Prefix:    /knowledgebase/
    显示名:         知识库                        [编辑]
    License:        ✓ 启用                        [禁用]
    注册时间:       2026-01-20
    --
    <b>影响范围 (License 禁用时)</b>
     * 10 个租户将无法访问该应用的任何接口
     * 已有的 resource_acl 数据保留，不会被清除
     * 重新启用后立即恢复
  }
  --
  [返回列表]                                      [下线应用 (危险)]
}
@endsalt
```

### 2.8 系统审计日志

```plantuml
@startsalt
{+
  {* 平台 | 租户 | 应用 | 路径规则 | <b>审计</b> }
  --
  {
    事件类型: ^全部^ | 操作者: "search..." | 时间: [2026-04-01] — [2026-04-14]  | [搜索]
  }
  --
  {#
    | 时间                | 操作者         | 事件                       | 详情                 | 来源 IP
    | 2026-04-14 10:12    | super-admin    | TENANT_CREATE              | customer-b           | 10.1.2.3
    | 2026-04-14 09:45    | super-admin    | APP_LICENSE_DISABLE        | memory               | 10.1.2.3
    | 2026-04-14 09:30    | super-admin    | LOGIN                      | success              | 10.1.2.3
    | 2026-04-14 09:10    | svc-crawler    | API_KEY_ROTATE             | key=ak_abc...        | 192.168.5.20
    | 2026-04-14 08:55    | tenant-admin@a | USER_CREATE                | zhangsan             | 10.2.3.4
  }
  --
  << 上一页  [1] [2] [3] ... [42]  下一页 >>                        [导出 CSV]
}
@endsalt
```

---

## 3 租户控制台 (tenant-admin)

### 3.1 租户概览

```plantuml
@startsalt
{+
  {* ^租户: customer-a^ | <b>概览</b> | 用户 | 用户组 | 应用 | 身份认证 | API Key | 审计 | 设置 | 👤 tenant-admin}
  --
  {# 关键指标
     | 用户    | 用户组   | 已启用应用  | 活跃 API Key
     | <b>230</b> | <b>8</b>    | <b>2 / 6</b>    | <b>12</b>
  }
  --
  {SI
    最近用户活动                                       | 快速操作
    ----                                              | ----
    10:01  lisi logged in via SAML                    | [+ 邀请用户]
    09:40  wangwu added to knowledgebase-admins       | [+ 新建组]
    09:15  api-key ak_abc... rotated                  | [+ 配置 IdP]
    08:50  zhaoliu password reset requested           | [+ 创建 API Key]
  }
  --
  {SI
    应用启用状态                                                        |
    ----                                                               |
    ✓ knowledgebase  (45 活跃用户)   [管理此应用资源]                   |
    ✓ memory         (230 活跃用户)  [管理此应用资源]                   |
    ✗ llm            (申请启用)      [提交申请]                        |
  }
}
@endsalt
```

### 3.2 用户列表

```plantuml
@startsalt
{+
  {* ^customer-a^ | 概览 | <b>用户</b> | 用户组 | 应用 | 身份认证 | API Key }
  --
  {
    搜索: "name/email..." | 所在组: ^全部^ | 状态: ^全部^ | [+ 邀请用户] | [批量导入 CSV]
  }
  --
  {#
    | ☐ | 用户名    | 邮箱                   | 所在组                         | 来源      | 最后登录        | 状态   | 操作
    | ☐ | zhangsan  | zhangsan@corp-a.com    | all-users, kb-admins          | SAML      | 2h ago         | 正常   | 详情 | 禁用
    | ☐ | lisi      | lisi@corp-a.com        | all-users                      | OIDC      | 1d ago         | 正常   | 详情 | 禁用
    | ☐ | wangwu    | wangwu@corp-a.com      | all-users, tenant-admins       | 本地      | 5m ago         | 正常   | 详情 | 禁用
    | ☐ | zhaoliu   | zhaoliu@corp-a.com     | all-users                      | SAML      | 30d+ ago       | 锁定   | 详情 | 解锁
  }
  --
  批量: [加入组 v] [移除组 v] [重置密码] [禁用]                      << [1] [2] [3] >>
}
@endsalt
```

### 3.3 用户详情

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>用户</b> / zhangsan }
  --
  {/ 基本信息 | 所在组 | 资源权限 | 登录历史 | 操作记录 }
  --
  {
    {
      | 头像:       [👤]                         [上传]
      | 用户名:     zhangsan                    (不可编辑)
      | 姓名:       张三                         [编辑]
      | 邮箱:       zhangsan@corp-a.com          [编辑]
      | 手机:       +86 138****8888              [编辑]
      | 身份来源:   SAML (customer-a-okta)
      | 创建时间:   2026-02-20
      | 状态:       ✓ 正常                       [禁用]
    }
  }
  --
  操作: [发送重置密码邮件]  [强制下线]  [踢出所有组]  [删除用户]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>用户</b> / zhangsan }
  --
  {/ 基本信息 | <b>所在组</b> | 资源权限 | 登录历史 | 操作记录 }
  --
  {
    当前所在组:
  }
  --
  {#
    | ☐ | 组名                      | 加入时间     | 操作
    | ☐ | all-users                 | 2026-02-20   | 移除
    | ☐ | knowledgebase-admins      | 2026-03-10   | 移除
    | ☐ | data-team                 | 2026-03-12   | 移除
  }
  --
  {
    加入新组:  ^搜索或选择组^                    [+ 加入]
  }
  --
  {SI
    权限预览 (基于当前组):
     * /api/v1/**                    tenant-admins 才能访问 → ✗
     * /knowledgebase/v1/admin/**    knowledgebase-admins 可访问 → ✓
     * /knowledgebase/v1/**          all-users 可访问 → ✓
     * /memory/v1/**                 all-users 可访问 → ✓
  }
}
@endsalt
```

### 3.4 用户组列表

```plantuml
@startsalt
{+
  {* ^customer-a^ | 用户 | <b>用户组</b> | 应用 | 身份认证 | API Key }
  --
  {
    搜索: "group..."                                                 | [+ 新建组]
  }
  --
  {#
    | 组名                    | 类型        | 成员数 | 关联路径规则              | 操作
    | tenant-admins           | 系统预置    | 2     | /api/v1/** (管理面)      | 详情
    | all-users               | 默认组      | 230   | 普通路径                  | 详情
    | knowledgebase-admins    | 应用预置    | 8     | /knowledgebase/admin/*    | 详情
    | memory-admins           | 应用预置    | 5     | /memory/admin/*           | 详情
    | data-team               | 自定义      | 30    | 无                        | 详情 | 删除
    | research-group          | 自定义      | 12    | 无                        | 详情 | 删除
  }
}
@endsalt
```

### 3.5 组详情

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>用户组</b> / knowledgebase-admins }
  --
  {
    组名:       knowledgebase-admins          (系统预置，不可改名)
    描述:       知识库应用管理员               [编辑]
    类型:       应用预置（knowledgebase）
    成员数:     8
    --
    <b>该组的权限说明</b>
     * 命中 path_rules: /knowledgebase/v1/admin/*  → 允许访问
     * 该组成员可管理 knowledgebase 应用的所有资源（owner）
  }
  --
  {/ <b>成员</b> | 关联路径规则 }
  --
  {
    搜索: "user..."                                                     | [+ 添加成员]
  }
  --
  {#
    | ☐ | 用户名    | 邮箱                    | 加入时间     | 操作
    | ☐ | wangwu    | wangwu@corp-a.com      | 2026-03-01   | 移除
    | ☐ | lisi      | lisi@corp-a.com        | 2026-03-05   | 移除
    | ☐ | zhangsan  | zhangsan@corp-a.com    | 2026-03-10   | 移除
  }
  --
  批量: [移除所选]                                                    << [1] [2] >>
}
@endsalt
```

### 3.6 身份认证 (IdP) 列表

```plantuml
@startsalt
{+
  {* ^customer-a^ | 用户 | 用户组 | 应用 | <b>身份认证</b> | API Key }
  --
  {/ <b>IdP 列表</b> | Client Mapper | IdP Mapper }
  --
  {
    已配置的外部身份源:                                              | [+ 新建 IdP]
  }
  --
  {#
    | Alias               | 协议    | 用户数 | 启用  | 测试连接 | 操作
    | customer-a-okta     | SAML    | 210   | ✓    | [测试]   | 详情 | 删除
    | customer-a-google   | OIDC    | 20    | ✓    | [测试]   | 详情 | 删除
    | legacy-ad           | SAML    | 0     | ✗    | [测试]   | 详情 | 删除
  }
}
@endsalt
```

### 3.7 新建 IdP（分步）

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>身份认证</b> / 新建 }
  --
  {/ <b>1. 协议</b> | 2. 连接配置 | 3. Mapper | 4. 测试 }
  --
  {
    选择认证协议:
    .
    (X) SAML 2.0
        企业级 SSO, AD/Okta/ADFS 等
    .
    ( ) OIDC (OpenID Connect)
        互联网 OIDC, Google/GitHub/微信 等
    .
    Alias (唯一, 用于 URL):  "customer-a-okta"
    显示名:                    "公司 Okta 登录"
  }
  --
  [取消]                                           [下一步 >]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>身份认证</b> / 新建 }
  --
  {/ 1. 协议 | <b>2. 连接配置</b> | 3. Mapper | 4. 测试 }
  --
  {
    SAML 配置方式:
    .
    (X) 导入 IdP Metadata (推荐)
        URL:        "https://okta.customer-a.com/app/xxx/sso/saml/metadata"  [抓取]
        或上传文件: [选择 XML 文件]
    .
    ( ) 手动填写
        Single Sign-On URL:     "..."
        Entity ID:              "..."
        X.509 证书:             "..."
  }
  --
  {
    回调 URL (需在 IdP 侧配置):
    .
    https://gateway.aidp.com/realms/customer-a/broker/customer-a-okta/endpoint    [复制]
  }
  --
  [< 上一步]                                       [下一步 >]
}
@endsalt
```

### 3.8 IdP Mapper 配置

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>身份认证</b> / customer-a-okta }
  --
  {/ 基本信息 | <b>Mapper</b> | 映射用户 | 测试 }
  --
  {SI
    IdP Mapper 作用: 外部 IdP 属性 → Keycloak 用户属性 / 自动加组
    --
    [外部 IdP] ─ SAML 断言 ─→ [Mapper 规则] ─→ [Keycloak 影子用户]
  }
  --
  {#
    | Mapper 名      | 类型                 | 源属性            | 目标             | 操作
    | email-mapper   | Attribute Importer   | email            | user.email       | 编辑 | 删除
    | name-mapper    | Attribute Importer   | displayName      | user.firstName   | 编辑 | 删除
    | dept-to-group  | Advanced Group       | Department=研发  | dev-team 组      | 编辑 | 删除
    | admin-detect   | Advanced Group       | role=admin       | tenant-admins    | 编辑 | 删除
  }
  --
  {+ + 新建 Mapper
  }
}
@endsalt
```

### 3.9 Client Mapper 配置

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>身份认证</b> / 客户端 data-agent }
  --
  {/ 基本信息 | <b>Protocol Mapper</b> | 测试 Token }
  --
  {SI
    Client Protocol Mapper 作用: Keycloak 用户信息 → JWT claims
    --
    [Keycloak 用户] ─→ [Mapper] ─→ [JWT token 输出的 claim]
  }
  --
  {#
    | Mapper 名                 | 类型                       | 源              | JWT claim        | 操作
    | groups                    | Group Membership           | user.groups    | groups           | 编辑 | 删除
    | tenant-injector           | Script Mapper              | realm name     | tenant_id        | 编辑 | 删除
    | email-mapper              | User Property              | email          | email            | 编辑 | 删除
  }
  --
  {+ + 新建 Protocol Mapper
  }
  --
  {SI
    JWT 预览 (以 zhangsan 登录为例):
    {
      "sub": "uuid-xxx",
      "iss": "https://gateway.aidp.com/realms/customer-a",
      "groups": ["all-users", "knowledgebase-admins"],
      "tenant_id": "customer-a",
      "email": "zhangsan@corp-a.com"
    }
  }
}
@endsalt
```

### 3.10 API Key 列表

```plantuml
@startsalt
{+
  {* ^customer-a^ | 用户 | 用户组 | 应用 | 身份认证 | <b>API Key</b> }
  --
  {
    搜索: "name..."   | 状态: ^全部^   | 身份: ^全部^                 | [+ 创建 API Key]
  }
  --
  {#
    | Name           | Prefix             | 关联身份            | 作用域                | 最后使用       | 过期时间      | 状态    | 操作
    | 爬虫服务       | ak_live_abcd...    | svc-crawler         | /knowledgebase/*      | 5m ago        | 永久          | 正常    | 轮换 | 禁用 | 删除
    | 数据同步脚本   | ak_live_efgh...    | zhangsan            | /memory/*             | 2d ago        | 2026-07-14    | 正常    | 轮换 | 禁用 | 删除
    | 旧版客户端     | ak_live_ijkl...    | svc-legacy          | /**                   | 30d+ ago      | 2026-05-01    | 即将过期 | 轮换 | 禁用 | 删除
    | 测试 Key       | ak_live_mnop...    | test-user           | /llm/*                | never         | 2026-04-20    | 已禁用   | 启用 | 删除
  }
}
@endsalt
```

### 3.11 创建 API Key（含一次性展示）

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>API Key</b> / 创建 }
  --
  {
    Name (用途描述):   "爬虫服务 Key"
    --
    关联身份:
    (X) 关联用户:      ^zhangsan^
    ( ) 服务账号:      ^svc-xxx^
    --
    作用域 (路径白名单):
    [X] /knowledgebase/v1/**
    [ ] /memory/v1/**
    [ ] /llm/v1/**
    [ ] 全部 (/**)
    --
    有效期:
    (X) 90 天
    ( ) 30 天
    ( ) 自定义: [2026-__-__]
    ( ) 永不过期 (不推荐)
    --
    速率限制:        "100"  req/min
  }
  --
  [取消]                                           [✓ 创建]
}
@endsalt
```

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>API Key</b> / 创建成功 }
  --
  {SI
    <b>⚠ 请立即复制，此密钥只会显示一次</b>
    --
    密钥明文:
    ak_live_Zf8xKJp2QmTv9Rn4wXcLdH3eYs6BnPoUi
    .
    [📋 复制到剪贴板]   [💾 下载为 .env]
    --
    30 秒后页面将自动跳转，此密钥不可再次查看。
    如遗忘，需轮换或重新创建。
  }
  --
  Key 详情:
   * Name:        爬虫服务 Key
   * Prefix:      ak_live_Zf8xKJp2
   * 关联身份:    zhangsan
   * 作用域:      /knowledgebase/v1/**
   * 过期时间:    2026-07-13
  --
                                                   [我已复制，继续]
}
@endsalt
```

### 3.12 API Key 轮换

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>API Key</b> / 爬虫服务 Key / 轮换 }
  --
  {SI
    <b>轮换 API Key</b>
    --
    当前 Key:      ak_live_abcd...
    新 Key:        将在确认后生成，仅展示一次
    --
    <b>轮换后的变化:</b>
     * 关联身份 (svc-crawler) 不变
     * resource_acl 中该身份的权限数据保留
     * 新 Key 立即可用
     * 旧 Key 保留 <b>[宽限期: ^24 小时^]</b> 后自动失效
     * 通知:  [ ] 发送邮件给关联身份  [ ] 发送 Webhook
  }
  --
  [取消]                                           [✓ 生成新 Key]
}
@endsalt
```

### 3.13 应用接入

```plantuml
@startsalt
{+
  {* ^customer-a^ | 用户 | 用户组 | <b>应用</b> | 身份认证 | API Key }
  --
  {#
    | App Name        | 显示名     | 状态       | 已授权用户数  | 操作
    | knowledgebase   | 知识库     | ✓ 已启用   | 45           | 进入应用控制台
    | memory          | 记忆库     | ✓ 已启用   | 230          | 进入应用控制台
    | llm             | LLM 路由   | ✗ 未启用   | —            | [申请启用]
    | fintech         | 金融分析   | ✗ 未启用   | —            | [申请启用]
  }
  --
  {SI
    提示: 应用启用/禁用由平台管理员控制。租户只能申请启用，实际生效后会在此显示绿色 ✓。
  }
}
@endsalt
```

---

## 4 应用控制台 (app-admin)

### 4.1 应用概览（以 knowledgebase 为例）

```plantuml
@startsalt
{+
  {* ^customer-a^ | <b>knowledgebase</b> | 资源 | 路径规则 | 应用组 | 👤 wangwu }
  --
  {# 关键指标
     | KB 总数  | Document 总数  | 活跃用户   | 今日 QPS
     | <b>128</b> | <b>2,450</b>      | <b>35</b>     | <b>420</b>
  }
  --
  {SI
    最近操作                                           | 待处理
    ----                                               | ----
    10:20  lisi created KB "客户调研 2026-Q2"           | [3 个权限申请待审批]
    09:55  zhangsan shared KB "产品白皮书" → data-team  | [0 个异常同步记录]
    09:30  wangwu deleted KB "草稿-旧版"                | .
  }
  --
  [返回租户控制台]
}
@endsalt
```

### 4.2 资源列表

```plantuml
@startsalt
{+
  {* <b>knowledgebase</b> / <b>资源</b> }
  --
  {
    搜索: "kb name..."  | 权限: ^全部^  | Owner: "search..."         | [批量导出 CSV]
  }
  --
  {#
    | ☐ | Resource ID  | 名称                | 类型  | Owner      | 分享数 | 我的权限    | 创建时间    | 操作
    | ☐ | kb-001       | 产品白皮书          | kb    | zhangsan   | 5      | owner       | 2026-04-10  | 分享 | 删除
    | ☐ | kb-002       | 客户调研 2026-Q2    | kb    | lisi       | 2      | contributor | 2026-04-14  | 分享
    | ☐ | kb-003       | 内部规范            | kb    | wangwu     | 0      | owner       | 2026-04-12  | 分享 | 删除
  }
  --
  批量: [批量分享] [导出]                                            << [1] [2] [3] >>
}
@endsalt
```

### 4.3 资源权限详情

```plantuml
@startsalt
{+
  {* <b>knowledgebase</b> / 资源 / kb-001 }
  --
  {
    名称:        产品白皮书
    Resource ID: kb-001
    类型:        kb
    Owner:       zhangsan (唯一)                          [转让]
    创建时间:    2026-04-10
  }
  --
  {/ <b>权限成员</b> | 操作日志 }
  --
  {
    当前成员:                                                        | [+ 分享]
  }
  --
  {#
    | ☐ | 主体          | 类型   | 权限          | 授权时间     | 操作
    | ☐ | zhangsan      | 用户   | owner         | 2026-04-10   | (唯一，不可改)
    | ☐ | lisi          | 用户   | contributor   | 2026-04-11   | 改权限 | 移除
    | ☐ | wangwu        | 用户   | viewer        | 2026-04-12   | 改权限 | 移除
    | ☐ | data-team     | 组     | viewer        | 2026-04-13   | 改权限 | 移除
    | ☐ | ak_live_abcd..| API Key | viewer       | 2026-04-14   | 改权限 | 移除
  }
  --
  批量: [回收所选]
}
@endsalt
```

### 4.4 分享资源

```plantuml
@startsalt
{+
  {* <b>knowledgebase</b> / 资源 / kb-001 / 分享 }
  --
  {
    分享给:
    .
    (X) 用户 / 组 (搜索):   "search user or group..."
    ( ) API Key:            ^选择 key^
    ( ) 全组织 (all-users)
    .
    权限:
    ( ) viewer (只读)
    (X) contributor (可编辑)
    ( ) owner (完全控制，含再分享)
    .
    有效期:
    (X) 永久
    ( ) 截止时间: [2026-__-__]
    .
    备注:  "客户项目协作"
  }
  --
  预览: 将授予 <b>data-team 组</b> 对 <b>kb-001 "产品白皮书"</b> 的 <b>contributor</b> 权限
  --
  [取消]                                           [✓ 分享]
}
@endsalt
```

---

## 5 个人中心

### 5.1 我的信息

```plantuml
@startsalt
{+
  {* AIDP | <b>个人中心</b> | 🔔 | 👤 zhangsan }
  --
  {/ <b>基本信息</b> | 安全设置 | 我的 API Key | 登录历史 }
  --
  {
    | 头像:       [👤]                                      [上传]
    | 用户名:     zhangsan                                  (不可改)
    | 显示名:     张三                                      [编辑]
    | 邮箱:       zhangsan@corp-a.com  ✓ 已验证              [更换]
    | 手机:       +86 138****8888                           [编辑]
    | 租户:       customer-a (客户 A)
    | 所在组:     all-users, knowledgebase-admins
    | 身份来源:   SAML (customer-a-okta)
  }
}
@endsalt
```

### 5.2 安全设置

```plantuml
@startsalt
{+
  {* AIDP | <b>个人中心</b> }
  --
  {/ 基本信息 | <b>安全设置</b> | 我的 API Key | 登录历史 }
  --
  {
    密码
      上次修改: 2026-02-15 (60 天前)                     [修改密码]
    --
    双因素认证 (MFA)
      ( ) 未启用
      (X) TOTP 验证码 (Google Authenticator 等)          [重新绑定] [禁用]
      ( ) WebAuthn / 安全密钥
    --
    登录设备
      * MacBook - Chrome 125        10.1.2.3  now         (本次会话)
      * iPhone - Safari             192.168.5.20  2h ago  [强制下线]
    --
    危险操作
      [退出所有会话]  [申请删除账号]
  }
}
@endsalt
```

### 5.3 我的 API Key（个人视角）

```plantuml
@startsalt
{+
  {* AIDP | <b>个人中心</b> }
  --
  {/ 基本信息 | 安全设置 | <b>我的 API Key</b> | 登录历史 }
  --
  {
    你名下的 API Key:                                              | [+ 创建 API Key]
  }
  --
  {#
    | Name           | Prefix             | 作用域          | 最后使用     | 过期时间     | 操作
    | 数据同步脚本   | ak_live_efgh...    | /memory/*       | 2d ago      | 2026-07-14   | 轮换 | 删除
  }
  --
  {SI
    说明: 你只能看到自己创建的 Key。租户管理员可查看并回收全部 Key。
  }
}
@endsalt
```

---

## 6 通用组件样式约定

### 6.1 顶部导航栏

```plantuml
@startsalt
{+
  {* <b>AIDP</b> | [角色视角切换 v] | [页面菜单] | 🔔 3 | 👤 user@domain v }
}
@endsalt
```

### 6.2 面包屑

```plantuml
@startsalt
{
  平台 / 租户 / customer-a / 用户 / zhangsan
}
@endsalt
```

### 6.3 二次确认（危险操作）

```plantuml
@startsalt
{+
  <b>⚠ 危险操作确认</b>
  --
  你即将删除租户 <b>customer-a</b>。此操作将:
   * 删除该租户下所有 230 个用户
   * 删除所有用户组
   * 删除所有 resource_acl 数据
   * 撤销所有 API Key
  --
  <b>此操作不可恢复。</b>
  --
  请输入租户 Realm 名确认: "customer-a"
  --
  [取消]                                           [✗ 永久删除]
}
@endsalt
```

### 6.4 空态提示

```plantuml
@startsalt
{+
  <b>📭 暂无数据</b>
  --
  还没有配置任何外部 IdP
  --
  [+ 立即配置第一个 IdP]   [查看文档]
}
@endsalt
```

### 6.5 加载骨架屏

```plantuml
@startsalt
{+
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  ▓▓▓▓▓▓▓▓▓
  --
  ▓▓▓▓▓▓▓▓▓▓▓ | ▓▓▓▓▓▓▓▓ | ▓▓▓▓▓▓▓▓▓▓
  ▓▓▓▓▓▓▓ | ▓▓▓▓ | ▓▓▓▓▓▓▓
}
@endsalt
```

---

## 7 信息架构总览

```plantuml
@startsalt
{T
 + AIDP 管理台
 ++ 登录
 ++ 平台控制台 (master-admin)
 +++ 概览
 +++ 租户管理
 ++++ 列表 / 新建 / 详情
 +++ 应用管理
 ++++ 列表 / 注册 / 详情
 +++ 路径规则
 +++ 审计
 ++ 租户控制台 (tenant-admin)
 +++ 概览
 +++ 用户管理
 ++++ 列表 / 详情 / 邀请 / 批量导入
 +++ 用户组管理
 ++++ 列表 / 详情 / 成员管理
 +++ 应用接入
 +++ 身份认证
 ++++ IdP 列表 / 新建 IdP / Mapper 配置
 +++ API Key 管理
 ++++ 列表 / 创建 / 轮换 / 禁用
 +++ 审计
 +++ 租户设置
 ++ 应用控制台 ({app}-admin)
 +++ 概览
 +++ 资源管理
 ++++ 列表 / 详情 / 分享 / 回收
 +++ 路径规则 (只读)
 +++ 应用组管理
 ++ 个人中心
 +++ 基本信息
 +++ 安全设置 (密码/MFA)
 +++ 我的 API Key
 +++ 登录历史
}
@endsalt
```

---

## 8 下一步

- 评审本线框图，提修改意见
- 确认后进入高保真设计（Figma/Sketch）
- 开发阶段：建议按 Section 8.2 分层 MVP 实现
  1. 第 1 层（MVP）：登录 + 平台租户/应用管理 + 租户用户/组/APIKey
  2. 第 2 层：IdP + Mapper + 应用控制台
  3. 第 3 层：审计、MFA、品牌定制
