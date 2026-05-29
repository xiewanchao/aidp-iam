# AI 原生开发流程 (AI-Native Dev Workflow)

**一句话定位：** 将 AI 嵌入需求、架构、代码、测试、部署、验收六个节点的全链路人机协作范式，不是"AI 写代码"，而是"AI 放大每一个人工决策"。

---

## TRIGGER — 何时启用本流程

满足以下任一条件时，按本流程组织工作：

| 条件 | 说明 |
|---|---|
| 新功能开发 | 需求尚未拆解，代码尚未动工 |
| 存量系统重构 | 有明确的重构目标，但边界未定 |
| 新成员接手项目 | 需要快速建立项目上下文 |
| 进入测试验收阶段 | 核心路径可跑通，需要切换到白+黑模式 |

**不适用场景：** 单文件 bugfix、配置微调、文档更新——这些直接操作，不走本流程。

---

## CONTEXT LOADING — 启动前加载

按需加载，不要一次性全部读入：

```
必须加载（每次）：
  CLAUDE.md                          ← 项目约束、部署命令、环境限制

按需加载（进入对应阶段时）：
  docs/architecture/                 ← 进入架构 / 代码阶段时
  docs/architecture/case-test.md    ← 进入测试用例生成阶段时
  memory/                            ← 遇到技术选型或历史决策时
  deploy/scripts/                    ← 进入构建 / 部署阶段时
  [日期]-issues.md                   ← 进入白+黑夜间修复时
```

**原则：** 上下文按需加载，不在会话开始时批量读入所有文档。每个阶段只加载该阶段需要的文件。

---

## SKILL PIPELINE — 与其他 Skill 的组合关系

本流程是主干管道，各阶段可调用专项 Skill：

```
ai-dev-workflow (本流程)
  ├── Phase 1: 需求澄清     → 可调用 ecc:prp-prd（生成结构化 PRD）
  ├── Phase 2: 架构设计     → 可调用 ecc:architect（架构草案）
  ├── Phase 3: 代码生成     → 可调用 ecc:feature-dev（功能开发）
  │                          → 可调用 ecc:code-reviewer（代码审查）
  ├── Phase 4: 测试生成     → 可调用 ecc:tdd-guide（TDD 工作流）
  ├── Phase 5: 构建部署     → 可调用 ecc:deployment-patterns（部署模式）
  │                          → 可调用 ecc:security-reviewer（安全审查）
  └── Phase 6: 白+黑验收   → 可调用 ecc:build-error-resolver（构建修复）
                             → 可调用 ecc:silent-failure-hunter（静默失败排查）
```

---

## WORKFLOW — 六阶段执行流程

### ▌Phase 0：阶段判断（每次启动时执行）

```
问题：当前处于哪个阶段？

→ 需求未结构化                        → 进入 Phase 1
→ 需求已定，架构未定                  → 进入 Phase 2
→ 架构已定，代码未完                  → 进入 Phase 3
→ 代码基本完成                        → 进入 Phase 4
→ 测试用例已有，脚本未生成            → 进入 Phase 5
→ 核心路径可跑通 + 测试通过率 > 80%  → 进入 Phase 6（白+黑）
```

---

### ▌Phase 1：需求结构化

**目标：** 把自然语言需求转化为带验收标准的结构化功能点，消除隐含约束。

**AI 行为：** 收到需求后，不直接给方案，先追问五个维度：

```
1. 隔离粒度    — 数据库级 / schema 级 / 行级 / 无
2. 鉴权位置    — 网关层 / 应用层 / 两者
3. 数据性质    — 静态配置 / 动态授权 / 混合
4. 外部依赖    — IdP 类型、消息队列、第三方 API 的 SLA
5. 性能基线    — 并发量、P99 延迟、数据规模
```

**人工决策点：** 确认功能点范围，拍板技术选型。AI 将选型理由写入 `docs/architecture/question-and-answer.md`。

**输出产物：**

```
docs/architecture/question-and-answer.md   ← 技术选型决策 + 被否定方案的原因
```

结构化功能点表格（内联在对话中）：

| 原始需求 | 扩展功能点 | 隐含约束 | 验收标准 |
|---|---|---|---|
| 支持资源级权限 | 前缀匹配 ACL 查询 | 子资源继承父资源权限 | 给定 user+object，返回最长匹配 ACL |

**✅ Phase 1 通过标准：**
- [ ] 所有功能点有明确的验收标准
- [ ] 技术选型已决策并记录原因
- [ ] 隐含约束已显式化，不依赖口头共识

---

### ▌Phase 2：架构设计

**目标：** 生成可执行的架构草案，让后续代码生成有明确的边界约束。

**加载：** `docs/architecture/`（如已有）、`CLAUDE.md`

**AI 输出三件套：**

1. **分层架构图**（ASCII / Mermaid）— 组件职责边界，每个服务只做一件事
2. **数据流时序图**— 覆盖主路径 + 至少两个故障路径
3. **DB Schema 草案**— 隔离边界在表结构层面显式体现

Schema 分层原则：
```sql
-- 系统级（无 tenant_id）：全局配置，所有租户共享
-- 租户级（有 tenant_id）：行级隔离，跨租户查询在 DB 层拦截
```

**人工决策点：** 架构评审，确认组件边界和数据隔离策略。

**输出产物：**
```
docs/architecture/request-flow.md     ← 数据流 + 时序图
docs/architecture/data-storage.md     ← Schema 设计 + 隔离策略
```

接口契约（OpenAPI 草稿）确认后锁定，后续不允许 AI 自行修改接口签名。

**✅ Phase 2 通过标准：**
- [ ] 架构图已评审，组件边界无歧义
- [ ] Schema 隔离策略已确认
- [ ] 接口契约已锁定（变更需人工决策）
- [ ] 架构文档已写入 `docs/architecture/`

---

### ▌Phase 3：代码生成与重构

**目标：** 在完整项目上下文里分模块生成代码，每个模块生成后立即验证。

**加载：** `CLAUDE.md`、`docs/architecture/`、`memory/`、相关现有代码

**生成顺序（按依赖关系，不跳步）：**

```
Step 1: DB schema        验证：建表 SQL 可执行，索引符合查询模式
Step 2: 核心业务逻辑     验证：主路径端到端可跑通
Step 3: 边界处理         验证：异常输入、并发、依赖不可用
Step 4: 集成层           验证：网关路由、K8s 资源、端到端鉴权链路
```

**约束感知：** AI 生成代码时自动遵守 `CLAUDE.md` 中的项目约束，无需用户每次提醒：
- API 路径规范（`/api/v1` 是旧版残留，新路径用 `app-onboarding`）
- K8s CRD 大小限制
- 镜像版本固定（不用 `latest`）
- 路径分隔符（Windows + Git Bash 环境）

**重构规则（与新功能开发严格分离）：**
```
1. 读取现有代码，理解当前设计意图（不假设）
2. 识别重构边界（接口签名不变，只改内部实现）
3. 生成重构后代码
4. 逐行对比差异，说明每处改动的原因
5. 运行现有测试，确认行为完全不变
```

> 重构不引入新功能，新功能不顺带重构。两者混提交是 code review 最难发现问题的场景。

**✅ Phase 3 通过标准：**
- [ ] 每个模块生成后立即验证，无积压未验证代码
- [ ] `bash deploy/scripts/test.sh` 通过率 > 80%
- [ ] 无硬编码密钥、无违反 `CLAUDE.md` 约束的代码

---

### ▌Phase 4：DT 测试用例生成

**目标：** 从业务视角系统性生成测试用例，覆盖人工最容易遗漏的依赖故障场景。

**加载：** `docs/architecture/case-test.md`

**为什么 AI 生成比人工更全面：**
- 人工盲区 1：依赖服务故障场景（Keycloak 宕机、OPA 不可用、DB 超时）在正常开发环境不出现，容易遗漏
- 人工盲区 2：多租户 × 多角色 × 多资源类型的权限组合，人工穷举成本极高
- AI 从业务逻辑推导，不从代码路径推导——测业务行为，不测实现细节

**生成约束（硬性规则）：**

| 规则 | 说明 |
|---|---|
| 三大场景必须覆盖 | 正常流程 + 异常操作 + 依赖服务故障 |
| 每个功能点标配 | 1 条正向 + 至少 1 条异常/故障，异常不超过 5 条 |
| 黑盒视角 | 不暴露 API 名称和内部参数 |
| 步骤与结果一一对应 | 测试步骤数 = 预期结果数 |
| 故障用例命名 | `xxx服务在XXX时故障，导致xxx功能失败` |
| 预置条件通用项 | 所有用例包含集群和基础服务正常的前提 |

**输出格式（可直接导入测试管理工具）：**
```csv
用例_名称,用例_编号,用例_预置条件,用例_测试步骤,用例_预期结果
```

**✅ Phase 4 通过标准：**
- [ ] 每个功能点有正向 + 故障两类用例
- [ ] 依赖服务故障场景已覆盖（Keycloak / OPA / DB）
- [ ] CSV 格式正确，可导入测试管理工具

---

### ▌Phase 5：构建与部署脚本生成

**目标：** 生成生产就绪的 Dockerfile、Helm Chart、CI/CD 流水线，安全检查内置于生成过程。

**加载：** `CLAUDE.md`、`deploy/scripts/`（现有脚本作为风格参考）

**Dockerfile 生成规则：**
```
✓ 多阶段构建（builder + runtime 分离，镜像体积减少 60–80%）
✓ 非 root 用户运行
✓ 固定基础镜像版本（不用 latest）
✓ 合并 RUN 指令（减少层数）
✓ 不在镜像里存放密钥或配置
```

**Helm Chart 标准结构：**
```
deploy/helm/<app>/
├── Chart.yaml
├── values.yaml          ← 默认值，可提交 Git
├── values-prod.yaml     ← 生产覆盖，不提交 Git
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml
    ├── secret.yaml      ← 引用 K8s Secret，不硬编码
    ├── hpa.yaml
    └── _helpers.tpl
```

**CI/CD 流水线阶段：**
```yaml
lint → test → build → scan(CVE) → push → deploy-dev → smoke-test → deploy-prod(人工审批)
```

**安全生成四项拦截（生成时内置，不依赖 review 发现）：**

| 风险 | 处理 |
|---|---|
| 硬编码密钥 | 替换为环境变量或 K8s Secret 引用 |
| 破坏性操作（`rm -rf`） | 加 `--dry-run` 或二次确认 |
| 生产 DB 直接操作 | 加环境检测，非 prod 不允许 |
| 跳过 hook（`--no-verify`） | 拒绝生成，除非用户明确说明原因 |

**✅ Phase 5 通过标准：**
- [ ] `docker build` 成功，健康检查通过
- [ ] Helm Chart `helm lint` 无错误
- [ ] 无硬编码密钥，无 `latest` 镜像引用
- [ ] CI 流水线包含安全扫描阶段

---

### ▌Phase 6：白 + 黑协作验收

**进入条件（必须同时满足，否则继续 Phase 3 迭代）：**

| 条件 | 检查方式 |
|---|---|
| 核心路径可跑通 | 主业务流程端到端无阻断性错误 |
| 集成测试通过率 > 80% | `bash deploy/scripts/test.sh` 实测结果 |
| 可部署到真实环境 | 能接受真实用户操作，不只是 mock 数据 |
| 问题性质已转变 | 从"功能缺失"变为"行为不符预期" |

> 未达到条件强行进入白+黑，测试人员大部分时间在等待而不是探索，浪费资源。

**人机分工逻辑：**

| 维度 | 人工（白天） | AI（夜间） |
|---|---|---|
| 发现问题 | 真实业务直觉、使用习惯、审美判断 | 系统性覆盖、不遗漏、不疲劳 |
| 修复问题 | 判断"设计问题还是代码问题" | 批量根因分析、跨文件一致性修复 |
| 时间分配 | 工作时间（精力充沛，适合探索） | 夜间（无需休息，适合批量处理） |

**☀️ 白天流程（人工主导，AI 不介入修复）：**
```
部署最新版本
  → 按真实业务场景操作（不按测试脚本，按实际使用习惯）
  → 记录问题日志（见下方格式）
  → 标注优先级（P0 阻断 / P1 影响体验 / P2 细节）
  → 下班前提交日志
```

白天纪律：不临时改代码、不打补丁、不跳过问题。临时补丁掩盖真实问题，让夜间 AI 修复错误目标。

**问题日志格式（`[日期]-issues.md`）：**
```markdown
## [日期] 测试问题记录

### P0 阻断
- [ ] **现象**：[操作步骤] → [实际结果] vs [预期结果]
  **环境**：[版本 / 账号类型 / 数据状态]
  **复现率**：必现 / 偶现（频率）

### P1 影响体验
- [ ] **现象**：...

### P2 细节
- [ ] **现象**：...
```

**🌙 夜间流程（AI 主导，不引入新功能）：**
```
读取全部问题日志（不跳过任何一条）
  → 分类：代码 bug / 配置问题 / 设计缺陷 / 环境问题
  → 按优先级排序（P0 → P1 → P2）
  → 逐条根因分析（沿调用链追溯，不停在表面现象）
  → 批量修复（相关问题合并处理，避免重复改同一文件）
  → 为每个修复生成对应回归测试用例
  → 提交代码 + 更新日志状态（已修复 / 待确认 / 设计问题需讨论）
```

夜间纪律：不引入新功能、不顺带重构、不改接口签名。

**☀️ 次日交接摘要（AI 输出）：**
```
昨日修复：X 个（P0: N，P1: N，P2: N）
待人工确认：X 个
设计问题待决策：X 个
新增回归用例：X 条
```

**✅ Phase 6 通过标准（验收完成）：**
- [ ] P0 问题清零
- [ ] P1 问题已修复或有明确的设计决策记录
- [ ] 回归测试覆盖所有已修复问题
- [ ] `bash deploy/scripts/test.sh` 通过率 > 95%

---

## MEMORY SYSTEM — 跨会话知识持久化

传统 AI 工具的根本缺陷：每次对话从零开始，已被否定的方案会被重新提出，调试过的陷阱会再次踩到。

本流程通过三层持久化解决，不依赖更大的上下文窗口：

| 层级 | 载体 | 内容 | 加载时机 |
|---|---|---|---|
| 项目约束层 | `CLAUDE.md` | 架构规则、部署命令、环境约束 | 每次会话自动加载 |
| 设计知识层 | `docs/architecture/` | 架构决策、数据流、故障模式 | 进入 Phase 2/3 时加载 |
| 隐性知识层 | `memory/` | 被否定方案、调试陷阱、用户偏好 | 遇到技术选型或历史决策时加载 |

**memory 文件结构：**
```markdown
---
name: short-kebab-case-slug
description: 一行摘要（用于判断相关性）
metadata:
  type: project | feedback | reference
---
核心内容

**Why:** 为什么有这条记忆
**How to apply:** 什么情况下应用
```

**三层文档的边界：**
- `docs/architecture/`：已确定的设计，是代码的"设计合同"
- `memory/`：被否定的方案、调试陷阱，是 AI 的"避坑手册"
- 代码本身：唯一的真相来源，文档是对代码的解释，不是替代

---

## ROOT CAUSE ANALYSIS — 根因分析协议

适用于 Phase 3 构建期验证和 Phase 6 夜间修复。

**追溯链（不停在表面现象）：**
```
现象（HTTP 状态码 / 错误消息）
  ↓ 输入：完整 JSON 报错体（不是"访问被拒绝了"这类描述）
调用链定位（哪个服务返回的错误）
  ↓
数据层验证（DB 数据是否符合预期）
  ↓
根因（代码逻辑 / 配置 / 初始化数据）
  ↓
影响范围评估（修改前先评估，再动手）
```

**实际案例：**
```
现象：GET /AccessManager/Tenants/{id}/Users/{id}/Details → 403
  ↓
OPA deny → path_rules 数据不匹配
  ↓
DB init SQL → WHERE NOT EXISTS 语义导致旧数据未被覆盖
  ↓
根因：存量数据问题，不是 OPA 逻辑问题
修复：补 UPDATE 语句处理存量数据，而不是改 OPA
```

---

## OUTPUT ARTIFACTS — 各阶段产出物清单

| 阶段 | 产出物 | 位置 | 格式 |
|---|---|---|---|
| Phase 1 | 技术选型决策 + 被否定方案 | `docs/architecture/question-and-answer.md` | Markdown |
| Phase 2 | 数据流 + 时序图 | `docs/architecture/request-flow.md` | Markdown + Mermaid |
| Phase 2 | Schema 设计 | `docs/architecture/data-storage.md` | Markdown + SQL |
| Phase 3 | 业务逻辑代码 | `apps/<service>/` | Python / Go / Java |
| Phase 4 | DT 测试用例 | `tests/` 或测试管理工具 | CSV |
| Phase 5 | Dockerfile | `build/docker/<service>/` | Dockerfile |
| Phase 5 | Helm Chart | `deploy/helm/<service>/` | YAML |
| Phase 5 | CI/CD 流水线 | `.github/workflows/` | YAML |
| Phase 6 | 问题日志 | `[日期]-issues.md` | Markdown |
| Phase 6 | 回归测试用例 | `tests/regression/` | CSV |

---

## PROMPT TEMPLATES — 可复用提示词

**Phase 1 需求澄清：**
```
我有一个新需求：[需求描述]
请先追问隐含约束（隔离粒度、鉴权位置、数据性质、外部依赖、性能基线），
不要直接给方案。澄清完成后输出结构化功能点表格。
```

**Phase 3 代码生成：**
```
读取 CLAUDE.md、docs/architecture/request-flow.md、docs/architecture/data-storage.md，
为 [模块名] 生成 [功能描述]。
按依赖顺序分模块生成，每个模块生成后说明验证方法。
生成后说明每个关键设计决策的原因。
```

**Phase 4 测试用例生成：**
```
读取 docs/architecture/case-test.md 中的规范，
基于以下业务功能描述和代码，生成标准化测试用例，输出 CSV 格式。
必须覆盖：正常流程、异常操作、依赖服务故障（Keycloak / OPA / DB）。
[业务功能描述]
[相关代码]
```

**Phase 6 夜间修复启动：**
```
读取 [日期]-issues.md，
按 P0 → P1 → P2 优先级逐条根因分析并修复。
修复完成后更新日志状态，为每个修复生成对应回归测试用例。
不引入新功能，不改接口签名，不顺带重构。
```

**根因分析：**
```
以下是完整的错误信息（JSON 报错体）：
[完整报错]
请沿调用链追溯根因，不要停在表面现象。
修复前先评估影响范围。
相关代码：[文件路径或代码片段]
```

---

## QUICK REFERENCE — 常用命令

```bash
# 全量重建 + 测试
bash deploy/scripts/setup.sh

# 快速迭代单个组件
bash deploy/scripts/rebuild.sh <app>
# app: iam-api | pep-proxy | bundle-server | resource-sync | gateway-manager

# 全量集成测试（Phase 3/6 验证用）
bash deploy/scripts/test.sh

# 网关黑盒测试
bash deploy/scripts/test-gateway.sh --k8s

# 清理环境
bash deploy/scripts/cleanup.sh
```

---

## METRICS — 效果量化参考

基于 aidp-iam 项目实际开发过程的观察：

| 场景 | 传统方式 | 本流程 | 加速比 |
|---|---|---|---|
| 新 API 端点（含鉴权） | 1–2 天 | 2–4 小时 | 4–6x |
| 新业务应用接入 | 3–5 天 | 半天 | 6–10x |
| 依赖故障测试用例设计 | 1–2 天（常遗漏） | 30 分钟（系统覆盖） | 4–8x |
| 分布式系统根因分析 | 2–8 小时 | 15–30 分钟 | 8–16x |
| 新成员项目上下文建立 | 1–2 周 | 1–2 天 | 5–10x |

---

## EXTENSION POINTS — 如何扩展本流程

| 扩展类型 | 操作 |
|---|---|
| 新增项目约束 | 写入 `CLAUDE.md`，AI 下次生成时自动遵守 |
| 记录被否定方案 | 写入 `docs/architecture/question-and-answer.md` |
| 记录调试陷阱 | 写入 `memory/` 对应文件 |
| 新业务应用接入 | 参考 `docs/onboarding/app-integration-guide.md` 生成 manifest + 路由 + 测试用例 |
| 新技术组件引入 | AI 生成 `*_first-introduction-review.md` + `*_selection-evaluation.md` |
| 流程本身改进 | 更新本文件对应阶段，同步更新 `memory/` 中的相关记忆 |
