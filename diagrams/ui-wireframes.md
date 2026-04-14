# AIDP IAM 管理台 UI 设计

> 版本：v1.0
> 日期：2026-04-14

---

## 1 顶层架构：按角色划分 3 套界面

不要把所有功能堆在一个界面里，**master-admin / tenant-admin / 普通用户**看到的东西不同：

```
┌─────────────────────────────────────────────────────────┐
│  登录后根据 JWT 中的 groups 判断进入哪套界面              │
└─────────────────────────────────────────────────────────┘
         │
         ├── groups: master-admins  → 平台控制台（Platform Console）
         ├── groups: tenant-admins  → 租户控制台（Tenant Console）
         ├── groups: {app}-admins   → 应用控制台（App Console，嵌入租户台）
         └── groups: all-users      → 业务应用自己的 UI（不在 IAM 范围）
```

---

## 2 平台控制台（master-admin 使用）

**一句话定位**：管理"整个平台"，跨租户操作。

### 主要页面

```
平台控制台
├── 概览仪表盘           # 租户数/用户数/应用数/QPS/最近活动
├── 租户管理 (Tenants)
│   ├── 租户列表
│   ├── 新建租户         # realm + 初始 admin（随机密码/邀请邮件）
│   ├── 租户详情
│   │   ├── 基本信息
│   │   ├── 用量统计
│   │   └── 冻结/删除
│   └── 租户下的 tenant-admin 账号
├── 应用管理 (Apps)
│   ├── 应用列表         # 所有注册的应用（system-level）
│   ├── 注册应用
│   │   ├── 基本信息：app_name, path_prefix, display_name
│   │   ├── 资源模式：resource_patterns（含 id_source/id_field）
│   │   └── 资源动作：resource_actions（非标准 RESTful 配置）
│   ├── 应用详情
│   │   ├── License 开关（enabled）
│   │   ├── 路径规则 (path_rules)
│   │   ├── 资源模式配置
│   │   └── 在各租户的启用情况
│   └── 下线应用
├── 路径规则 (Path Rules)
│   ├── 规则列表         # 所有 path_rules（system-level）
│   ├── 新建/编辑规则    # path_prefix + required_group
│   └── 按应用分组查看
├── 系统配置
│   ├── 密钥轮换
│   └── 审计日志查询
└── 个人中心
    ├── 修改密码
    └── 我的 API Key
```

### 关键 UI 决策

- **"新建租户"用多步向导**（Wizard）：步骤 1 填基本信息 → 步骤 2 配置 admin 交付方式（邀请邮件/随机密码）→ 步骤 3 确认 → 完成页展示一次性密码（可复制、30 秒后隐藏）
- **"注册应用"右侧实时预览生成的 Rego 策略**，让开发者直观感受 path_rules 效果
- **资源模式编辑器**要有"测试"按钮：输入示例 URL，实时显示提取到的 resource_id（验证 `id_source: path/body/query` 配置正确）

---

## 3 租户控制台（tenant-admin 使用）

**一句话定位**：管自己租户内的一切。

```
租户控制台（当前租户：data-agent）
├── 概览
│   ├── 用户/用户组统计
│   ├── 已启用应用数
│   └── 近期活动
├── 用户管理 (Users)
│   ├── 用户列表         # 支持搜索、按组过滤、按状态过滤
│   ├── 新建用户         # 姓名/邮箱/初始组（默认 all-users）
│   ├── 用户详情
│   │   ├── 基本信息 + 编辑
│   │   ├── 所属组（支持批量加入/移除）
│   │   ├── 登录历史
│   │   ├── 重置密码 / 发送激活邮件
│   │   └── 禁用/删除
│   └── 批量导入（CSV）
├── 用户组管理 (Groups)
│   ├── 组列表（tenant-admins / all-users / {app}-admins / 自定义组）
│   ├── 新建自定义组     # 部门/项目组
│   ├── 组详情
│   │   ├── 组成员（列表+添加+移除）
│   │   └── 组的权限说明（哪些 path_rules 提到它）
│   └── 删除组
├── 应用接入 (Apps)
│   ├── 已启用应用列表   # 从平台获取 + 本租户启用情况
│   ├── 应用详情（跳转到该应用的管理控制台）
│   └── 申请启用新应用（提交给 master-admin 审批）
├── 身份认证 (Identity Providers)
│   ├── IdP 列表         # 配置了几个外部 IdP
│   ├── 新建 IdP
│   │   ├── 选择协议：SAML / OIDC
│   │   ├── 配置参数（metadata URL / 证书 / client_id/secret）
│   │   └── 测试连接
│   ├── IdP 详情
│   │   ├── 基本配置
│   │   ├── Mapper 配置  # 见下文
│   │   └── 已映射的用户
│   └── 删除 IdP
├── Mapper 配置
│   ├── Client Protocol Mappers    # JWT 输出哪些 claim
│   │   ├── groups（默认）
│   │   ├── tenant_id（Script Mapper）
│   │   └── 自定义 claim
│   ├── IdP Attribute Mappers       # 外部 IdP 属性如何映射
│   │   ├── 邮件 → email
│   │   ├── 部门 → 自动加入对应组
│   │   └── 自定义规则
│   └── 新建 Mapper（分 Client / IdP 两种类型）
├── API Key 管理 (API Keys)
│   ├── Key 列表（只显示前缀 + 创建时间 + 最后使用时间）
│   ├── 新建 Key
│   │   ├── 关联的用户身份 / 服务账号
│   │   ├── 有效期 / 限流配置
│   │   └── 创建后一次性展示明文
│   ├── Key 详情（不能再看明文，只能轮换）
│   ├── 轮换 Key
│   └── 禁用 / 删除 Key
├── 审计日志
│   ├── 登录日志
│   ├── 权限变更日志
│   └── API Key 使用记录
└── 租户设置
    ├── 密码策略
    ├── 登录安全（MFA、锁定策略）
    └── 品牌定制（Logo / 主题色）
```

### 用户管理细节

- **"用户列表"支持按 `应用+权限等级` 过滤**：比如"有 knowledgebase 应用 contributor 权限以上的用户"
- **"所属组"显示双视图**：当前属于哪些组（tenant-admins ✓、all-users ✓、knowledgebase-admins ✗）+ 点击勾选立即生效
- **激活邮件/重置密码**：tenant-admin 不直接看到新密码，而是触发 Keycloak 的 `execute-actions-email`（UPDATE_PASSWORD）

### IdP + Mapper 配置的关键

这是最容易绕晕的地方，UI 要**把抽象概念可视化**：

```
[IdP 配置] 配置 SAML/OIDC 连接参数
      ↓
[IdP Mapper] 外部 IdP 属性 → Keycloak 用户属性 / 自动加组
      例：saml 属性 "Department=研发" → 自动加入 dev-team 组
      ↓
[用户登录] Keycloak 创建影子用户，按 Mapper 规则填充属性
      ↓
[Client Mapper] Keycloak 用户信息 → JWT claims
      例：user.groups → JWT.groups
      ↓
[JWT 给后端] 后端根据 groups 做鉴权
```

UI 上做一个**流程图交互式引导**，点每个节点展开配置，配置完就能看到"登录后 JWT 长什么样"的预览。

---

## 4 应用控制台（{app}-admin 使用）

**一句话定位**：管具体应用的资源权限（knowledgebase、memory 等）。

按应用独立一套（knowledgebase-admin 只看知识库）：

```
应用：knowledgebase
├── 资源概览
│   ├── 资源总数（KB / Document）
│   └── 各租户分布
├── 资源管理 (Resources)
│   ├── 资源列表         # 从 resource_acl 查
│   ├── 资源详情
│   │   ├── 资源信息
│   │   ├── Owner（唯一）
│   │   ├── Contributors（多个）
│   │   ├── Viewers（多个）
│   │   └── 授权历史
│   ├── 批量分享
│   └── 权限回收
├── 路径规则（只读）     # 该应用配置的 path_rules
└── 应用内用户组
    └── knowledgebase-admins 成员管理
```

---

## 5 应用权限配置：是否分应用？

**是，必须分应用**。理由：

1. **权限模型本身按 app 隔离**：path_rules 和 resource_actions 都带 app_name
2. **UX 合理**：knowledgebase-admin 不该看到 memory-admin 的资源
3. **扩展性**：新应用接入时直接挂载到控制台，不用改平台代码

**技术实现**：应用控制台可以设计成**可插拔 UI 模块**（类似 VSCode 扩展）：

```
tenant-console/
├── core/                    # 租户控制台骨架
│   ├── layout/
│   ├── users/
│   ├── groups/
│   └── router.ts
├── app-modules/
│   ├── knowledgebase/       # 每个应用一个模块
│   │   ├── routes.ts
│   │   └── pages/
│   │       ├── KBList.vue
│   │       └── KBShare.vue
│   └── memory/
│       └── ...
```

注册新应用时，平台也下发对应的前端模块 URL，动态加载。

---

## 6 API Key 管理：增删改查的正确做法

### 关键 UX 原则

**明文只展示一次**。这是行业铁律（AWS、GitHub、Stripe 都这样）。

### 流程设计

```
[点击 "新建 API Key"]
     ↓
[填写表单]
  - Name: 用途描述
  - 关联身份: 用户 / 服务账号 / 指定 group
  - 作用域: 哪些路径可用（从 path_rules 选）
  - 有效期: 30 天 / 90 天 / 永不过期
  - 速率限制: 100 req/min
     ↓
[后端生成 + hash 存 DB]
     ↓
[一次性展示页]
  ┌──────────────────────────────────────┐
  │  ⚠ 请立即复制，此密钥只会显示一次       │
  │                                      │
  │  API Key:                            │
  │  aidp_live_xxxxxxxxxxxxxxxxxxxx     │
  │  [复制到剪贴板]  [下载 .env 文件]      │
  │                                      │
  │  30 秒后该页面将关闭                   │
  └──────────────────────────────────────┘
     ↓
[跳转到 Key 列表]
  只显示 key_prefix (aidp_live_xxxx...) + metadata
```

### 列表字段

| 字段 | 显示 |
|---|---|
| Name | 用户填的描述 |
| Prefix | `aidp_live_abcd...`（前 8 位） |
| 关联身份 | 用户名/服务账号 |
| 作用域 | 路径 tags |
| 创建时间 | 相对时间（3 天前） |
| 最后使用 | 相对时间 + IP |
| 状态 | Active / Expired / Revoked |
| 操作 | 轮换 / 禁用 / 删除 |

### 轮换 vs 重建

- **轮换（Rotate）**：subject_id 不变，换一个新 Key。`resource_acl` 里的数据保留，业务系统无感知
- **删除后重建**：subject_id 变了（新用户/服务账号），权限需重新授权

UI 上两个按钮都要有，并做清楚区分。

---

## 7 技术栈与组件建议

### 框架
- **Vue 3 + TypeScript + Element Plus** 或 **React + TypeScript + Ant Design Pro**
- 路由：Vue Router / React Router
- 状态：Pinia / Redux Toolkit
- OIDC 登录：`oidc-client-ts`（浏览器端直接对接 Keycloak，拿 token）

### 推荐组件
- **表格**：ProTable（Ant Design Pro）或 Element Plus Table + 自定义封装
- **表单**：支持"分步表单 + 条件显示 + 动态字段"
- **树形选择**（组/权限）：Element Tree 或 Ant Tree
- **JSON 编辑器**（resource_patterns、Rego 预览）：Monaco Editor

### 鉴权
- 前端用 `oidc-client-ts` 拿 JWT
- 所有 API 请求带 `Authorization: Bearer <token>`
- 前端路由根据 JWT 的 groups 动态渲染菜单（隐藏无权限的页面）
- 关键操作双保险：前端隐藏 + 后端 `path_rules` 强制校验

---

## 8 设计稿分层建议

```
第 1 层（MVP）：
  - 登录 + 切换租户
  - 平台：租户管理（创建/列表）+ 应用管理（注册/列表）
  - 租户：用户管理 + 用户组管理
  - API Key 管理（创建/列表/删除）

第 2 层（完整 IAM）：
  + 平台：路径规则
  + 租户：IdP 配置 + Mapper 配置
  + 应用控制台（knowledgebase/memory 各一套）

第 3 层（企业级）：
  + 审计日志
  + 品牌定制
  + 批量导入/导出
  + MFA
```

---

## 9 关键交互细节

### 对 tenant-admin 友好

- **面包屑固定**：任何页都能看到"当前租户：data-agent"
- **上下文切换**：master-admin 点击某租户后进入"模拟 tenant-admin 视角"（有明显红色标记"正在以租户身份操作"）
- **权限预览**：任何组/用户的详情页都有"TA 现在能访问什么"的预览（自动求解 path_rules + resource_acl）

### 错误提示

不要只报"403 Forbidden"。要翻译成用户能懂的：

```
❌ 你没有权限执行此操作
   原因：该操作需要 tenant-admins 组权限
   你当前的组：all-users, knowledgebase-admins
   联系你的租户管理员获取权限
```

---

## 10 核心设计原则总结

这个 UI 的核心设计原则：

1. **按角色分控制台**，不是把所有功能堆一个界面
2. **应用权限按 app 独立模块**，可插拔扩展
3. **抽象概念可视化**（IdP→Mapper→JWT 流程图）
4. **密钥一次性展示 + 前缀标识**（API Key 行业规范）
5. **危险操作都有二次确认 + 预览后果**（删除租户、轮换 Key 等）
