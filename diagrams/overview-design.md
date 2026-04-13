# AIDP IAM 鉴权认证组件 — 概要设计 (v2.1)

## 2 概述

本项目面向AIDP平台，建设一套统一的鉴权认证公共服务底座，覆盖平台内各核心业务场景。通过统一接入入口（Envoy Gateway）、统一身份认证（Keycloak）、统一权限模型（租户、用户组、应用、路径规则、资源ACL）和统一策略执行引擎（OPA/OPAL），实现一个平台、一套认证鉴权标准、一致的访问控制行为。各场景无需重复建设权限系统，只需按标准注册应用和资源模式即可获得多租户隔离、两级细粒度控制（路径级 + 资源实例级）、ACL自动同步、动态策略分发、实时生效和可审计的访问治理能力，从而降低建设与运维成本，提升安全合规性与交付效率。

| 价值维度   | 说明                                                         |
| ------ | ---------------------------------------------------------- |
| 身份联合接入 | 客户无需改变现有身份系统，通过 SAML/OIDC 协议联合对接，员工使用已有账号即可登录，降低客户接入成本     |
| 统一安全边界 | 所有服务共享同一套认证授权基础设施，避免各服务重复实现鉴权逻辑，消除安全短板                     |
| 动态策略管理 | 应用与路径规则支持运行时修改，OPA 策略经 OPAL 实时同步，无需重新部署或重启服务即可生效，满足业务快速迭代的需要 |
| 多租户隔离  | 数据与权限天然按租户隔离，新客户接入不影响已有客户，支持独立的用户组体系和资源访问控制               |
| 业务零侵入  | 下游服务只需读取 Gateway 注入的 X-Auth-* Headers，不涉及 JWT 解析、密钥管理等鉴权细节；资源创建/删除的 ACL 由 ext_proc 自动同步，业务无需额外对接 |
| 统一流量治理 | Envoy Gateway 层天然支持负载均衡、流量控制（限流）、超时重试、TLS 终止、CORS 管理、可观测性等能力 |
| 合规审计基础 | ext_authz 链路天然记录了"谁、在什么时间、属于什么用户组、访问了什么资源"的完整审计信息         |

## 2.1 目的

本文档以需求分析文档及SR或AR分析为指导，对AIDP中的鉴权认证功能进行设计，明确主要数据结构和处理流程，作为今后编码阶段的输入和编码人员、测试人员的指导。

## 3 特性相关需求

|   |   |   |   |   |
|---|---|---|---|---|
|**需求编号**|**需求名称**|**特性描述文档名**|**特性描述**|**备注**|
|IR20260408001212|AIDP支持统一鉴权和网关服务|/|Gateway统一流量入口：<br><br>1. 基于 Envoy Gateway v1.7.0 提供统一 API Gateway 服务，作为 AIDP 平台流量入口，支持 HTTPS 终结、路由级 metrics 输出、路由分发。<br><br>2. 支持 ext_authz 机制（SecurityPolicy），所有业务请求经 Gateway 统一鉴权拦截后路由至后端服务，业务组件无需自行实现鉴权。SecurityPolicy 配置 bodyToExtAuth（maxRequestBytes=8192）以支持请求体中资源ID的提取。<br><br>3. 支持 ext_proc 机制（EnvoyExtensionPolicy），在响应阶段自动拦截资源创建和删除响应，由 resource-sync 实现 ACL 自动同步。<br><br>4. 支持 IP 白名单控制，限制非授权来源访问。<br><br>身份认证（Keycloak）：<br><br>5. 支持联邦身份认证，通过 SAML 2.0/OIDC 1.0 协议对接客户 IdP，用户无需重复注册。<br><br>6. 支持 JWT Token 签发与管理，已登录用户携带 JWT 即可访问全平台服务。支持 API Key 认证，适用于程序化访问场景。<br><br>7. 支持多租户隔离，每个租户独立 Realm，用户、组相互隔离。<br><br>资源鉴权（两级）：<br><br>8. 提供基于用户组的权限框架，支持三层用户组模型（master-admins、tenant-admins、all-users）。<br><br>9. 支持基于 OPA 的路径级动态策略鉴权（path_rules），管理员组自动放行。<br><br>10. 支持资源实例级鉴权（resource_acl），根据请求动作映射最低权限等级（GET→viewer、PUT/PATCH→contributor、DELETE→owner），子资源继承父资源权限。||

特性：

1）统一身份认证：基于 Keycloak，支持多租户 Realm、用户/用户组管理与外部 IDP 联合登录，支持 API Key 认证；

2）统一鉴权入口：Envoy Gateway 挂载 ext_authz（SecurityPolicy），在入口层统一拦截和放行；

3）两级鉴权模型：路径级鉴权（pep-proxy → OPA，基于 apps + path_rules）+ 资源实例级鉴权（pep-proxy → resource_acl），形成纵深防御；

4）ACL 自动同步：ext_proc 拦截资源创建/删除响应，resource-sync 自动维护 resource_acl，失败操作进入 pending_acl 异步重试；

5）灵活 API 适配：通过 resource_patterns（id_source: path/query/body）和 resource_actions 表支持非标准 RESTful API 的资源鉴权；

6）动态策略引擎：OPA + OPAL + bundle-server，策略由 apps + path_rules 生成 Rego bundle，热更新、近实时生效；

7）多租户隔离：按 tenant 维度隔离用户组、资源 ACL 与 API Key；

8）标准化 API：提供应用注册、路径规则 CRUD、资源 ACL 管理、API Key 管理等接口。

解决的问题：

1）解决各系统重复建设认证鉴权，标准不统一的问题；

2）解决多租户场景下的跨租户越权风险；

3）解决资源实例级权限需业务自行实现的问题，通过 ACL 自动同步实现业务零侵入。

## 4.1 特性概述

该鉴权认证组件基于 Envoy Gateway + Keycloak + OPA 构建，为 AIDP 各业务场景可复用的公共能力层，需求来源如下：1）客户侧对多租户统一身份接入、细粒度授权（路径级 + 资源实例级）、策略可动态调整的刚性需求；2）平台内部对鉴权链路标准化、策略治理集中化、发布运维成本下降的优化需求。该组件通过 Envoy Gateway 实现流量入口统一拦截与 ext_authz 前置鉴权，Keycloak 提供标准身份认证与租户/用户组上下文，OPA 提供可编排、可热更新的路径级策略决策，pep-proxy 在路径鉴权通过后进一步执行资源实例级 ACL 校验，resource-sync 通过 ext_proc 自动维护资源 ACL，从而形成认证与授权解耦、策略与代码解耦、资源权限与业务解耦的架构。

该组件能帮助客户实现更快交付，减少项目定制开发；更高安全一致性，同一策略框架覆盖多场景；更低变更风险，策略在线调整无需重发版；业务零侵入，资源创建/删除自动同步 ACL，无需业务对接权限系统。如果缺乏该组件，客户通常会落回各系统各自实现鉴权的烟囱模式，导致权限口径不一致，运维与审计成本上升等。同时在与同类平台竞争时会缺少统一安全底座及快速场景复制的核心竞争力。

## 5.1 总体方案

**流量控制模块（Envoy Gateway）：**

基于 Envoy Gateway v1.7.0（Envoy 内核）构建统一的流量入口。所有外部请求通过 Gateway 的单一端口进入系统，由 HTTPRoute 规则进行路径匹配和后端分发。Gateway 通过 SecurityPolicy 配置 ext_authz 外接 pep-proxy，在请求到达业务后端之前拦截并调用策略层进行鉴权判定（路径级 + 资源实例级）。通过 EnvoyExtensionPolicy 配置 ext_proc 外接 resource-sync，在响应阶段自动完成资源 ACL 同步。这一层的核心价值在于将"流量如何路由"、"请求是否放行"以及"资源权限如何同步"统一管理，业务服务无需关心网络拓扑、安全策略和资源权限维护。

**身份模块（Keycloak）：**

基于 Keycloak 构建企业级身份联合平台。每个客户对应一个独立的 Realm（租户），通过 SAML/OIDC Identity Provider 联合客户已有的身份系统，将客户的不同鉴权协议归一到 OIDC。员工通过 OIDC 登录后，Keycloak 在本地创建影子用户并签发标准化的 JWT Token，Token 中包含租户标识（iss 字段中的 realm）和结构化用户组信息（groups + group_ids）。同时支持 API Key 认证，api_keys 表存储于 iam Postgres，适用于程序化访问场景。这一层将"用户是谁"的问题标准化，下游无需关心客户原来用的是 AD 还是企业微信。

**策略模块（OPA + 资源 ACL）：**

基于 Open Policy Agent 构建动态策略评估引擎，结合资源 ACL 实现两级鉴权。pep-proxy 作为策略执行点（PEP），接收 Gateway 的 ext_authz gRPC 请求，从 JWT 中提取用户身份和用户组信息：

- **第一级——路径鉴权**：pep-proxy 查询 OPA，基于 Rego 策略进行判定：应用是否启用？用户组是否被 path_rules 允许？管理员组自动放行。策略数据通过 bundle-server 从 iam Postgres（apps + path_rules 表）生成 Rego bundle，经 OPAL 实时同步到 OPA 实例。
- **第二级——资源实例级鉴权**：路径鉴权通过后，pep-proxy 查询 iam Postgres 的 resource_acl 表，根据请求动作映射最低权限等级（GET→viewer、PUT/PATCH→contributor、DELETE→owner），子资源继承父资源权限。非标准 RESTful API 通过 resource_actions 表自定义动作映射。

这一层将"用户能做什么"的逻辑从业务代码中完全剥离，支持运行时动态变更。

## 多租户结构

1. 系统预置 master tenant（Keycloak master realm），预置 master-admins 用户组，预置 default master admin 账户。

2. 用户使用 master admin 创建 tenant，对接用户自己的 domain。

3. tenant 创建后预置 tenant-admins 用户组、all-users 用户组，以及 default tenant admin 账户。

4. tenant admin 管理 tenant 下用户组、用户、应用启用、路径规则配置。

5. 普通用户（all-users 组）在所属 tenant 下登录后使用对应业务服务。

6. 管理面（keycloak-proxy、pep-proxy 路径规则管理）通过 Gateway 路由访问，master-admins 和 tenant-admins 用户组拥有管理权限。

## 实体关系图

| 实体                | 所属系统                | 存储位置                   | 说明                                                 |
| ----------------- | ------------------- | ---------------------- | -------------------------------------------------- |
| Tenant (Realm)    | Keycloak            | keycloak DB            | 一个 Realm = 一个客户                                    |
| User              | Keycloak            | keycloak DB            | 客户员工，通过 IDP 联合创建的影子用户                              |
| Group             | Keycloak            | keycloak DB            | 用户组：master-admins、tenant-admins、all-users，JWT 携带 groups + group_ids |
| IDP               | Keycloak            | keycloak DB            | SAML/OIDC 身份源配置                                    |
| Client            | Keycloak            | keycloak DB            | OIDC 客户端，每个租户初始化一个，承载 Token 签发                     |
| ProtocolMapper    | Keycloak            | keycloak DB            | 挂在 Client 上，控制 JWT Token 输出字段（groups、group_ids 等） |
| IDPMapper         | Keycloak            | keycloak DB            | 挂在 IDP 上，将外部 IDP 属性（SAML/OIDC）映射为 Keycloak 内部属性/用户组 |
| App               | bundle-server       | iam Postgres (apps)    | 注册的业务应用，系统级（无 tenant_id）                           |
| ResourcePattern   | bundle-server       | iam Postgres (resource_patterns) | 资源模式定义，含 id_source（path/query/body）、id_field、id_query_param |
| ResourceAction    | bundle-server       | iam Postgres (resource_actions) | 非标准 RESTful 动作映射（app_name, resource_prefix, method, path_suffix, action, min_permission） |
| PathRule          | pep-proxy           | iam Postgres (path_rules) | 路径级访问规则，定义哪些用户组可访问哪些路径，系统级          |
| ResourceACL       | resource-sync       | iam Postgres (resource_acl) | 资源实例级权限（tenant 级），记录 user 对具体资源的权限等级（owner/contributor/viewer） |
| PendingACL        | resource-sync       | iam Postgres (pending_acl) | ACL 同步失败的重试队列                                      |
| APIKey            | keycloak-proxy      | iam Postgres (api_keys) | API Key 认证凭证，tenant 级，关联用户身份                       |

## 5.2.1 设计思路

**统一网关的定位**

Envoy Gateway v1.7.0 是系统的统一流量入口，承担以下职责：

流量收口：所有外部请求通过 Gateway 的单一端口进入系统，由 HTTPRoute 规则进行路径匹配和后端分发，避免各服务独立暴露端口带来的管理复杂度和安全风险。

安全边界：Gateway 通过 SecurityPolicy 配置 ext_authz 外部授权，在请求到达业务后端之前拦截并调用 pep-proxy 完成 AuthN + AuthZ 判定（路径级 + 资源实例级），所有受保护路由默认拒绝访问（Default Deny），通过鉴权后才放行。SecurityPolicy 配置 bodyToExtAuth（maxRequestBytes=8192）转发请求体，支持从请求体中提取资源 ID。

ACL 自动同步：Gateway 通过 EnvoyExtensionPolicy 配置 ext_proc，将资源创建/删除的响应流转发给 resource-sync，自动维护 resource_acl 表，实现业务零侵入的资源权限管理。

身份透传：鉴权通过后，Gateway 将 JWT 中的身份信息（tenant、user、groups、group_ids）转移到 HTTP Headers（X-Auth-*），业务后端直接读取 Headers 即可获得已验证的用户身份，无需引入 JWT 库或管理密钥。

流量治理：基于 Envoy 内核，Gateway 天然具备负载均衡、重试、TLS 终止、限流、可观测性等能力，为后续扩展提供基础。

**核心设计决策**

**单一 Gateway 资源，多 namespace 路由：** 系统只创建一个 Gateway 资源（namespace envoy-gateway-system，GatewayClass eg，Gateway 名称 eg），监听 :80 端口，通过 allowedRoutes.namespaces.from: All 允许所有 namespace 注册 HTTPRoute。搭配 ReferenceGrant 实现跨 namespace 的安全引用，避免了多 Ingress 的管理复杂度。

**路由分为鉴权组和免鉴权组：** Keycloak OIDC 端点（/realms/\*, /resources/\*, /admin/\*）走免鉴权路由——用户需要先登录才能获取 Token，登录端点本身不能要求 Token。其余 API 路由通过 SecurityPolicy 挂载 ext_authz 策略，统一走 pep-proxy 鉴权。

**ext_authz 采用 gRPC 协议：** 相比 HTTP ext_authz，gRPC 模式传输效率更高，支持结构化的 CheckRequest/CheckResponse。pep-proxy 从请求中提取 JWT 并验签，无需 Gateway 层预处理。

**请求体转发支持 body-based 资源 ID 提取：** SecurityPolicy 配置 bodyToExtAuth.maxRequestBytes=8192，将请求体转发给 pep-proxy，使其能够根据 resource_patterns 中 id_source=body 的配置从请求体中提取资源 ID，支持非标准 RESTful API（如 POST 创建类接口）的资源级鉴权。

**默认 Deny + 鉴权后 Header 注入：** 所有受保护路由默认拒绝。鉴权通过后，pep-proxy 通过 CheckResponse 返回 X-Auth-* Headers，Gateway（Envoy）自动将这些 Headers 注入到转发给上游的请求中，业务后端对鉴权过程完全无感知。

## 5.2.2 功能描述

| 功能 | 说明 |
|---|---|
| **HTTPRoute 路由** | 基于 Gateway API HTTPRoute CRD 的路径匹配和后端分发，支持 PathPrefix 匹配，路由定义集中在 da-cluster/gateway-routes/ |
| **OIDC 客户端交互** | WebUI 使用 oidc-client-ts 与 Gateway 交互，Gateway 透传 OIDC 请求到 Keycloak |
| **ext_authz 两级鉴权** | 对受保护路由通过 SecurityPolicy 配置 gRPC ext_authz 调用 pep-proxy，完成路径级鉴权（OPA）+ 资源实例级鉴权（resource_acl） |
| **ext_proc ACL 同步** | 通过 EnvoyExtensionPolicy 配置 ext_proc 调用 resource-sync，自动拦截资源创建/删除响应，维护 resource_acl |
| **身份透传 + Header 注入** | 鉴权通过后将 JWT 中的身份信息（tenant、user、groups、group_ids）转移到 X-Auth-* Headers |
| **请求体转发** | SecurityPolicy 配置 bodyToExtAuth.maxRequestBytes=8192，支持从请求体提取资源 ID |
| **默认 Deny** | 所有受保护请求默认拒绝，仅通过 AuthN + AuthZ 后放行 |
| **跨命名空间路由** | 通过 ReferenceGrant 实现 envoy-gateway-system → keycloak/opa 的跨 namespace 路由 |
| **API Key 认证** | 支持通过 API Key 进行程序化访问认证，pep-proxy 从请求头提取 API Key 并验证 |
| **列表过滤** | resource-sync 通过 ext_proc 为列表请求注入 X-Allowed-Ids Header，业务后端据此过滤返回结果 |

## 5.2.4 资源分析

| 组件                        | 角色  | 建议 Requests (CPU/Mem) | 建议 Limits (CPU/Mem) | QoS 等级    | 理由                                                       |
| ------------------------- | --- | --------------------- | ------------------- | --------- | -------------------------------------------------------- |
| envoy-gateway controller  | 管理面 | 100m / 256Mi          | 500m / 1024Mi       | Burstable | Go 应用，负责监听 Gateway API CRD 并生成 Envoy 配置，启动和 reconcile 时内存波动较大 |
| envoy-gateway proxy (Envoy) | 数据面 | 100m / 128Mi          | 500m / 512Mi        | Burstable | C++ Envoy 数据面，需容纳 ext_authz + ext_proc 过滤器链及连接缓冲区         |
