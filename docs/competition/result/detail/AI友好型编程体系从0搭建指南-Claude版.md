# AI 友好型编程体系从 0 搭建指南（Claude 版）

本文回答一个落地问题：如果一个团队现在只有一个普通代码仓，想用 Claude 作为主要 AI 编程工具，如何从 0 搭建一套可持续运行的 AI 友好型编程流程。

这里的“AI 友好型编程”不是安装一个工具、写几个 Prompt，也不是把所有代码交给 AI 自动生成，而是把项目约束、架构、测试、Issue、经验、Skill 和验证命令组织成一套 AI 能读懂、能执行、能验证、能继承的工程系统。

## 1. 最终要搭成什么样

目标不是“让 Claude 会写代码”，而是让 Claude 在明确边界里参与完整工程闭环：

```text
需求 / Bug / 运维任务
  -> Claude 读取项目约束和任务上下文
  -> 结构化需求或问题
  -> 先生成测试规约或复现用例
  -> 再修改代码、配置、脚本和文档
  -> 自动运行验证命令
  -> 输出证据、风险和待人工确认点
  -> 把高价值经验沉淀为记忆或 Skill
```

搭建完成后，仓库应该具备以下能力：

| 能力 | 说明 | 典型资产 |
| --- | --- | --- |
| Claude 能理解项目 | 启动后知道项目结构、技术栈、命令、禁区和代码风格 | `CLAUDE.md` |
| Claude 能按流程做事 | 不同任务走不同流程，不把新需求、Bug、重构混在一起 | `docs/ai/ai-dev-workflow.md` |
| Claude 能先写测试 | 新需求和 Bug 修复先形成验收标准、失败测试或回归用例 | `docs/testing/test-as-spec.md` |
| Claude 能复用专家经验 | 高频任务沉淀为 Skills，减少每次手写 Prompt | `.claude/skills/*/SKILL.md` |
| Claude 能被人工触发固定动作 | 常用流程沉淀为 slash commands | `.claude/commands/*.md` |
| Claude 能跨会话继承 | 被否定方案、架构决策、调试陷阱不丢失 | `memory/`、`docs/architecture/` |
| Claude 能自动验证 | 测试、构建、部署、健康检查有明确命令入口 | `scripts/`、`deploy/scripts/` |
| Claude 能安全停止 | 权限、生产环境、敏感数据和高风险操作有边界 | `CLAUDE.md`、`/permissions`、Issue 模板 |

## 2. 前置准备

### 2.1 工具准备

建议先准备以下基础工具：

| 工具 | 用途 |
| --- | --- |
| Claude Code | 主要 AI 编程工具，运行在项目终端中 |
| Git | 分支、差异、回滚、提交和交互记录追踪 |
| 项目本身的构建工具 | 例如 Maven、Gradle、npm、pnpm、Go、Python、Docker、Helm |
| 测试命令 | 单测、集成测试、黑盒测试、冒烟测试 |
| 本地或测试环境 | 支持 Claude 运行验证，不直接碰生产 |

Claude Code 内建议至少熟悉这些命令：

| 命令 | 用途 |
| --- | --- |
| `/doctor` | 检查 Claude Code 安装和环境状态 |
| `/init` | 初始化项目级 `CLAUDE.md` |
| `/memory` | 查看和编辑 Claude 记忆文件 |
| `/permissions` | 查看或调整 Claude 的命令权限 |
| `/clear` | 清空当前会话，重新加载项目上下文 |
| `/compact` | 压缩长会话，保留关键上下文 |
| 自定义 `/xxx` 命令 | 执行团队沉淀的固定 Prompt |

### 2.2 项目准备

一个普通仓库在接入 AI 友好型流程前，至少要先确认：

1. 仓库已进入 Git 管理。
2. 可以在本地或测试环境跑起来。
3. 有最基本的构建或测试命令。
4. 生产密钥、账号、隐私数据没有写在仓库里。
5. 团队能接受“AI 修改必须有证据”的工作方式。

如果项目当前没有测试，也可以接入，但第一阶段目标要改为“先建立最小验证闭环”，不能直接进入白+黑或夜间自动修复。

## 3. 推荐目录结构

建议把 AI 友好型工程资产直接放进仓库，跟代码一起版本化。

```text
repo/
├── CLAUDE.md
├── docs/
│   ├── ai/
│   │   ├── ai-dev-workflow.md
│   │   ├── context-loading.md
│   │   ├── task-types.md
│   │   └── prompt-templates.md
│   ├── architecture/
│   │   ├── overview.md
│   │   ├── request-flow.md
│   │   ├── data-storage.md
│   │   └── decision-records.md
│   ├── specs/
│   │   └── feature-xxx.md
│   └── testing/
│       ├── test-as-spec.md
│       ├── regression-policy.md
│       └── failure-matrix.md
├── memory/
│   ├── README.md
│   ├── decisions/
│   ├── pitfalls/
│   ├── prompts/
│   └── runbooks/
├── .claude/
│   ├── commands/
│   │   ├── new-feature.md
│   │   ├── bugfix.md
│   │   ├── night-run.md
│   │   └── write-memory.md
│   └── skills/
│       ├── ai-dev-workflow/
│       │   └── SKILL.md
│       ├── context-engineering/
│       │   └── SKILL.md
│       ├── tdd-spec/
│       │   └── SKILL.md
│       ├── root-cause-analysis/
│       │   └── SKILL.md
│       ├── white-black-night-fix/
│       │   └── SKILL.md
│       └── safe-devops/
│           └── SKILL.md
├── scripts/
│   ├── setup.sh
│   ├── test.sh
│   ├── smoke.sh
│   └── cleanup.sh
└── src/ 或 apps/
```

目录设计原则：

1. `CLAUDE.md` 放“每次都必须知道”的项目约束。
2. `docs/ai/` 放流程规则，回答“AI 应该怎么工作”。
3. `docs/architecture/` 放架构事实，回答“系统为什么这样设计”。
4. `docs/testing/` 放测试规约，回答“什么行为算正确”。
5. `memory/` 放跨会话经验，回答“哪些坑不能再踩”。
6. `.claude/skills/` 放可复用能力，回答“遇到特定任务时 Claude 应采用什么专家流程”。
7. `.claude/commands/` 放可人工触发的固定流程，回答“我输入什么命令可以启动某类任务”。
8. `scripts/` 放可执行验证命令，回答“Claude 改完以后如何证明它是对的”。

## 4. 第一步：建立项目级 `CLAUDE.md`

`CLAUDE.md` 是 Claude Code 的项目记忆入口。它不应该写成长篇设计文档，而应该写成“项目宪法”：短、硬、明确、每次会话都值得加载。

### 4.1 初始化

在项目根目录启动 Claude Code 后执行：

```text
/init
```

然后人工整理 `CLAUDE.md`。建议结构如下：

```markdown
# Project Instructions

## Project Role
你是本项目的 AI 编程协作者。你的目标不是尽快写代码，而是在理解项目约束、测试规约和风险边界后，完成可验证的工程修改。

## Architecture Summary
- 本项目的核心模块有哪些。
- 每个模块负责什么。
- 哪些接口或数据结构不能随意修改。

## Must Read Before Coding
- 新需求：读取 docs/ai/ai-dev-workflow.md、docs/testing/test-as-spec.md、相关 specs。
- Bug 修复：读取 issue、日志、相关测试和调用链文档。
- 部署运维：读取 scripts/、deploy/ 和 runbooks。

## Commands
- 构建：...
- 单测：...
- 集成测试：...
- 冒烟测试：...
- 清理：...

## Coding Rules
- 遵守现有代码风格。
- 不做无关重构。
- 不混合新功能和重构。
- 修改公共接口前必须说明影响范围并等待人工确认。

## Testing Rules
- 新需求必须先有测试规约。
- Bug 修复必须先复现或补回归用例。
- 修改后必须说明运行了哪些验证命令，结果是什么。

## Security Rules
- 不读取、不输出、不提交密钥。
- 不直接操作生产环境。
- 不执行破坏性命令，除非用户明确确认。
- 生产权限、数据库变更、安全策略变更必须交还人工审批。

## Output Rules
- 每次完成任务后输出：改了什么、如何验证、剩余风险。
- 如果信息不足，先列出缺口，不要猜测式大改。
```

### 4.2 `CLAUDE.md` 不能写什么

不要把以下内容直接塞进 `CLAUDE.md`：

1. 大段架构细节。
2. 所有历史 Bug。
3. 完整 API 文档。
4. 长篇需求背景。
5. 全量测试用例。
6. 敏感环境变量和账号。

这些内容应该放进 `docs/` 或 `memory/`，再通过索引和按需加载让 Claude 使用。否则每次会话都会浪费上下文，反而降低准确度。

## 5. 第二步：建立上下文资产目录

Claude 改代码不准确，常见原因不是模型不会写，而是上下文边界不清楚。因此第二步要把项目变成“可被 AI 导航”的形态。

### 5.1 让 Claude 先做代码仓体检

可以使用这个 Prompt：

```text
请先不要修改代码。
请阅读当前仓库结构，输出一份 AI 上下文地图：
1. 主要模块和职责。
2. 核心请求链路。
3. 关键配置和启动命令。
4. 测试入口。
5. 高风险目录和不要随意修改的文件。
6. 你认为后续新需求、Bug 修复、部署运维分别需要加载哪些上下文。

输出到 docs/ai/context-loading.md。
```

人工 review 这份文档，重点看：

1. 模块边界是否理解正确。
2. 测试命令是否能真实执行。
3. 高风险文件是否标对。
4. 是否漏掉关键外部依赖。

### 5.2 上下文加载规则模板

`docs/ai/context-loading.md` 可以这样写：

```markdown
# Context Loading Rules

## Always Load
- CLAUDE.md
- docs/ai/ai-dev-workflow.md

## New Feature
- docs/specs/<feature>.md
- docs/testing/test-as-spec.md
- related source files
- related existing tests

## Bug Fix
- issue record
- full error response
- related logs
- related tests
- request-flow document

## Refactor
- target module
- public interface definition
- existing tests
- behavior compatibility notes

## DevOps
- deploy scripts
- environment runbook
- health check commands
- latest failure logs
```

关键原则是“最小上下文闭包”：只加载当前任务真正需要的材料，不把所有文档一次性塞进上下文。

## 6. 第三步：固化 AI 开发主流程

在 `docs/ai/ai-dev-workflow.md` 中写清楚 Claude 遇到不同任务时应该走什么流程。

推荐把任务分成四类：

| 任务类型 | 流程重点 | 不允许做的事 |
| --- | --- | --- |
| 新项目 / 大模块 | 先需求、架构、测试，再实现 | 不直接铺代码 |
| 新增需求 | 先 Spec 和测试规约，再代码 | 不顺带重构 |
| Bug 修复 | 先复现和根因，再最小修复 | 不只改表面现象 |
| 自动运维 | 先健康检查和日志归因，再报告或低风险修复 | 不碰生产和破坏性操作 |

### 6.1 主流程模板

```markdown
# AI Dev Workflow

## Trigger
当用户提出新需求、Bug 修复、重构、部署验证、夜间修复任务时启用。

## Phase 0：阶段判断
- 需求未澄清 -> Phase 1
- 架构未确认 -> Phase 2
- 测试未定义 -> Phase 3
- 代码待实现 -> Phase 4
- 需要部署验证 -> Phase 5
- 进入验收稳定期 -> Phase 6

## Phase 1：需求结构化
输出功能点、边界、验收标准、待人工确认问题。

## Phase 2：架构设计
输出组件边界、数据流、接口契约、风险点。

## Phase 3：测试即规约
输出正常、异常、依赖故障、安全负向测试。

## Phase 4：实现
按最小可验证单元修改代码、配置、脚本、文档。

## Phase 5：验证
运行约定命令，保存结果和失败证据。

## Phase 6：白+黑验收
白天收集 issue，夜间批量归因、修复、回归，次日人工验收。
```

### 6.2 人工决策点

流程里必须显式标出哪些事情不能由 Claude 自行决定：

1. 架构边界变更。
2. 公共接口签名变更。
3. 权限语义变更。
4. 数据库破坏性迁移。
5. 生产环境操作。
6. 删除大量代码或文件。
7. 引入新依赖或新基础设施。

这些决策点是 AI 友好型工程的安全阀。

## 7. 第四步：把测试前置为规约

如果没有测试，Claude 很容易写出“看起来合理，但不符合业务语义”的代码。因此必须先建立 `docs/testing/test-as-spec.md`。

### 7.1 测试规约模板

```markdown
# Test As Specification

## Principle
实现前先定义测试。测试不只是验证代码是否能跑，而是定义业务行为是否成立。

## Required Test Types
1. 正常流程：功能应该如何成功。
2. 异常输入：参数缺失、格式错误、状态不允许。
3. 安全负向：无权限、越权、跨租户、过期凭证。
4. 依赖故障：DB、缓存、外部服务不可用。
5. 回归测试：每个已修复 Bug 必须有对应防回退用例。

## TDD Workflow
需求 -> Spec -> 测试矩阵 -> 失败测试 -> 实现 -> 测试通过 -> 文档更新。

## Bugfix Workflow
现象 -> 复现用例 -> 失败测试 -> 根因分析 -> 最小修复 -> 回归通过。
```

### 7.2 新需求 Prompt

```text
我要做一个新需求：[需求描述]
请不要直接写代码。
请先按以下结构输出：
1. 需要澄清的问题。
2. 结构化功能点。
3. 验收标准。
4. 正常流程测试。
5. 异常和安全负向测试。
6. 依赖故障测试。
7. 哪些测试需要先失败。
确认后再进入实现。
```

### 7.3 Bug 修复 Prompt

```text
以下是一个 Bug：
- 现象：
- 复现步骤：
- 实际结果：
- 预期结果：
- 日志：

请先不要修代码。
请输出：
1. 需要补充的复现信息。
2. 可能的调用链。
3. 最小复现测试或回归测试。
4. 根因分析计划。
5. 修改边界。
确认测试或复现方案后再修改代码。
```

## 8. 第五步：建立长短期记忆系统

AI 友好型编程必须解决跨会话遗忘问题。推荐把记忆分为短期和长期。

### 8.1 短期记忆

短期记忆只服务当前任务或当天任务：

| 类型 | 示例 |
| --- | --- |
| 当前 issue | 今天测试发现的问题 |
| 当前错误日志 | 本轮失败的完整输出 |
| 当前分支目标 | 这次只修哪些问题 |
| 当前验证结果 | 哪些测试通过，哪些失败 |

短期记忆可以存在：

- 当前 Claude 会话。
- 当天 issue 文件。
- 当前分支的任务清单。
- 临时测试报告。

### 8.2 长期记忆

长期记忆用于跨会话、跨项目复用：

```text
memory/
├── README.md
├── decisions/
│   └── 0001-auth-model.md
├── pitfalls/
│   └── 0001-db-init-not-overwrite.md
├── prompts/
│   └── bug-root-cause.md
└── runbooks/
    └── local-deploy.md
```

长期记忆建议使用统一模板：

```markdown
---
title: 简短标题
type: decision | pitfall | prompt | runbook
scope: project | module | team
created: YYYY-MM-DD
---

## What
这条记忆是什么。

## Why
为什么需要记录。

## When To Use
遇到什么任务时加载。

## How To Apply
Claude 应该如何使用。

## Do Not
哪些误用要避免。
```

### 8.3 什么时候沉淀为长期记忆

满足以下任一条件，就应该沉淀：

1. AI 第二次犯同类错误。
2. 某个方案被明确否定。
3. 某个调试结论耗时超过 30 分钟。
4. 某个测试命令或部署命令经常被用到。
5. 某个权限、安全、数据规则影响多个需求。
6. 某个 Prompt 被重复使用超过 3 次。

### 8.4 `CLAUDE.md` 与 `memory/` 的边界

| 资产 | 放什么 | 不放什么 |
| --- | --- | --- |
| `CLAUDE.md` | 每次都必须遵守的硬规则 | 大段案例和历史过程 |
| `docs/architecture/` | 已确认架构和接口契约 | 临时想法 |
| `memory/` | 决策、坑点、Prompt、Runbook | 未验证猜测 |
| issue 日志 | 当天问题和复现信息 | 长期规则 |

## 9. 第六步：建立 Skills

Skills 是把专家流程包装成 Claude 可自动发现的能力。相比每次手写 Prompt，Skill 更适合沉淀稳定、重复、跨任务的工作方法。

Claude Code 的项目 Skills 建议放在：

```text
.claude/skills/<skill-name>/SKILL.md
```

个人 Skills 可以放在：

```text
~/.claude/skills/<skill-name>/SKILL.md
```

项目 Skills 适合团队共享，个人 Skills 适合个人习惯和实验能力。

### 9.1 Skill 与其他资产的区别

| 资产 | 谁触发 | 适合放什么 |
| --- | --- | --- |
| `CLAUDE.md` | 每次会话自动加载 | 项目硬规则 |
| Skill | Claude 判断相关时自动使用 | 某类任务的专家流程 |
| Slash Command | 用户手动输入 `/command` 触发 | 固定入口 Prompt |
| `memory/` | 按需加载 | 历史经验和决策 |
| MCP / 外部工具 | 工具调用 | 数据库、浏览器、Issue 系统、CI 等外部能力 |

### 9.2 推荐 Skill 清单

| Skill | 触发场景 | 输入 | 输出 |
| --- | --- | --- | --- |
| `ai-dev-workflow` | 新项目、新需求、Bug、运维任务开始时 | 任务描述、项目上下文 | 阶段判断、执行计划、质量门禁 |
| `context-engineering` | Claude 对项目理解不足、跨会话接续 | 任务目标、相关目录 | 最小上下文闭包、需读取文件清单 |
| `tdd-spec` | 新需求、Bug 修复前 | 需求或问题描述 | 测试矩阵、失败测试、验收标准 |
| `root-cause-analysis` | 测试失败、线上问题、复杂 Bug | 日志、错误响应、调用链 | 根因链路、影响范围、最小修复建议 |
| `white-black-night-fix` | 白+黑夜间修复 | issue 列表、验证命令、修改边界 | 修复计划、逐项状态、次日摘要 |
| `safe-devops` | 部署、重建、健康检查、日志归因 | 环境、脚本、日志 | 运维报告、风险提示、人工确认点 |

如果团队已有 `ecc` 等 Skill 包，可以按如下方式映射：

| AI 友好型流程 | 可承接的 ecc 类 Skill |
| --- | --- |
| 需求结构化 | `ecc:prp-prd` |
| 架构设计 | `ecc:architect` |
| 功能开发 | `ecc:feature-dev` |
| 代码审查 | `ecc:code-reviewer` |
| TDD / 测试规约 | `ecc:tdd-guide` |
| 部署流程 | `ecc:deployment-patterns` |
| 构建错误修复 | `ecc:build-error-resolver` |
| 静默失败排查 | `ecc:silent-failure-hunter` |

这里的关键不是 Skill 名字，而是把流程拆成可复用能力：需求、架构、实现、测试、部署、修复、复盘。

### 9.3 `tdd-spec` Skill 示例

```markdown
---
name: tdd-spec
description: Use when a new feature or bug fix needs tests, acceptance criteria, or regression cases before implementation.
---

# TDD Spec Skill

## Goal
Turn requirements or bugs into executable or reviewable test specifications before code changes.

## When To Use
- User asks for a new feature.
- User reports a bug.
- Existing behavior is ambiguous.
- Security or permission logic is involved.

## Inputs To Request
1. Business goal or bug symptom.
2. Expected behavior.
3. Current behavior.
4. Related API or module.
5. Existing tests and commands.

## Workflow
1. Do not edit code first.
2. Identify missing constraints.
3. Produce test matrix:
   - normal path
   - negative path
   - dependency failure
   - security boundary
   - regression case
4. Ask for confirmation if behavior is ambiguous.
5. Only after tests/spec are accepted, proceed to implementation.

## Output Format
- Clarifying questions
- Test matrix
- Proposed failing tests
- Implementation boundary
- Verification command

## Stop Conditions
- Business behavior is unclear.
- No way to verify the change.
- Change requires public API or security policy decision.
```

### 9.4 `root-cause-analysis` Skill 示例

```markdown
---
name: root-cause-analysis
description: Use when a test, deployment, API call, or production-like workflow fails and needs call-chain based diagnosis.
---

# Root Cause Analysis Skill

## Goal
Find the underlying cause of a failure instead of patching the surface symptom.

## Required Evidence
- Full error response.
- Reproduction steps.
- Logs from involved services.
- Recent code/config changes.
- Relevant test command and output.

## Analysis Chain
1. What is the visible symptom?
2. Which component returned the error?
3. What input did that component receive?
4. What downstream dependency did it call?
5. Is the data/config correct?
6. Is this code bug, config issue, data drift, environment problem, or design gap?
7. What is the smallest safe fix?

## Rules
- Do not guess from status code alone.
- Do not widen the scope without evidence.
- Do not change public contracts unless approved.
- Add or update regression test after fixing.
```

### 9.5 `white-black-night-fix` Skill 示例

```markdown
---
name: white-black-night-fix
description: Use for night-time batch issue fixing after human testers record structured issues during the day.
---

# White Black Night Fix Skill

## Goal
Process a structured issue list without human interruption, while keeping risk bounded.

## Entry Conditions
- Core workflow can run.
- Issue list has steps, actual result, expected result, environment, priority.
- Verification command is available.
- Work is on an isolated branch or worktree.

## Workflow
1. Read all issues.
2. Sort P0 -> P1 -> P2.
3. For each issue:
   - reproduce or explain missing evidence
   - classify type
   - perform root cause analysis
   - make minimal fix
   - run targeted test
   - update issue status
4. Run broader regression at the end.
5. Produce next-day handoff summary.

## Stop Conditions
- Missing reproduction data.
- Design decision required.
- Production permission required.
- Two consecutive fix attempts fail.
- Scope exceeds issue boundary.
```

## 10. 第七步：建立 Slash Commands

Slash commands 适合做“人工明确启动”的流程入口。它和 Skill 的区别是：Skill 是 Claude 自动判断，slash command 是用户显式触发。

项目级 commands 放在：

```text
.claude/commands/
```

### 10.1 `/new-feature`

`.claude/commands/new-feature.md`：

```markdown
请按 AI 友好型新需求流程处理以下需求：

$ARGUMENTS

执行规则：
1. 不要直接写代码。
2. 先读取 CLAUDE.md、docs/ai/ai-dev-workflow.md、docs/testing/test-as-spec.md。
3. 输出澄清问题、结构化 Spec、测试矩阵、实现边界。
4. 等待我确认后，再进入代码实现。
```

### 10.2 `/bugfix`

`.claude/commands/bugfix.md`：

```markdown
请按 AI 友好型 Bug 修复流程处理以下问题：

$ARGUMENTS

执行规则：
1. 不要先改代码。
2. 先补充复现信息和失败测试。
3. 沿调用链做根因分析。
4. 只做最小修复。
5. 修复后运行验证命令并说明结果。
```

### 10.3 `/night-run`

`.claude/commands/night-run.md`：

```markdown
请启动白+黑夜间修复流程：

$ARGUMENTS

执行规则：
1. 读取 issue 列表。
2. 按 P0 -> P1 -> P2 排序。
3. 每个 issue 先复现、再归因、再最小修复。
4. 每个修复必须有验证证据。
5. 遇到设计问题、权限问题、生产风险立即停止该 issue 并标记待人工确认。
6. 最后输出次日交接摘要。
```

### 10.4 `/write-memory`

`.claude/commands/write-memory.md`：

```markdown
请把以下经验整理成长期记忆：

$ARGUMENTS

输出到 memory/ 中合适的目录，格式包含：
1. What
2. Why
3. When To Use
4. How To Apply
5. Do Not

不要记录密钥、账号、客户数据或敏感日志。
```

## 11. 第八步：建立自动验证命令

没有可执行验证，AI 友好型流程会退化为“AI 说它改好了”。因此必须给 Claude 明确的验证入口。

### 11.1 最小命令集

| 命令 | 用途 |
| --- | --- |
| `scripts/test.sh` | 单测或核心测试 |
| `scripts/smoke.sh` | 冒烟验证 |
| `scripts/lint.sh` | 静态检查 |
| `scripts/setup.sh` | 本地环境初始化 |
| `scripts/cleanup.sh` | 清理测试环境 |

如果是 Kubernetes / Helm 项目，可以扩展：

| 命令 | 用途 |
| --- | --- |
| `deploy/scripts/setup.sh` | 安装测试环境 |
| `deploy/scripts/test.sh` | 集成测试 |
| `deploy/scripts/test-gateway.sh` | 网关黑盒测试 |
| `deploy/scripts/cleanup.sh` | 清理环境 |
| `deploy/scripts/reinstall.sh` | 重装验证 |

### 11.2 命令设计要求

1. 能非交互执行。
2. 失败时返回非零退出码。
3. 日志包含足够定位信息。
4. 不依赖人工输入密码。
5. 不默认操作生产环境。
6. 可以在 README 或 `CLAUDE.md` 中说明。

### 11.3 验证输出要求

Claude 每次完成修改后必须说明：

```text
验证命令：
- xxx

验证结果：
- 通过 / 失败

失败时：
- 失败用例
- 错误摘要
- 初步归因
- 下一步建议

未运行的验证：
- 原因
- 风险
```

## 12. 第九步：接入四类实践流程

### 12.1 新项目 / 大模块开发

适用场景：

- 新服务。
- 新模块。
- 大规模接入新能力。
- 架构边界未定。

流程：

```text
业务目标
  -> Claude 追问约束
  -> 架构草案
  -> 人工评审
  -> 测试规约
  -> 模块拆解
  -> 分模块实现
  -> 自动验证
  -> 文档和记忆沉淀
```

推荐 Prompt：

```text
我要从 0 开发一个新模块：[模块目标]
请按照 AI 友好型流程工作：
1. 先追问需求和边界。
2. 输出架构草案和接口契约。
3. 输出测试规约。
4. 给出分模块实现计划。
5. 不要直接写代码，等我确认。
```

### 12.2 新增需求

适用场景：

- 新 API。
- 新页面。
- 新配置项。
- 新业务接入。

流程：

```text
需求描述
  -> 澄清问题
  -> Spec
  -> 测试矩阵
  -> 失败测试
  -> 最小实现
  -> 回归
```

关键规则：

1. 需求没澄清前不写代码。
2. 测试没定义前不写代码。
3. 实现不顺带重构。
4. 改公共契约必须人工确认。

### 12.3 Bug 修复

适用场景：

- 测试失败。
- 用户反馈异常。
- 日志报错。
- 权限、安全、数据异常。

流程：

```text
现象
  -> 复现步骤
  -> 失败测试
  -> 根因分析
  -> 最小修复
  -> 回归验证
  -> 记忆沉淀
```

关键规则：

1. 不从状态码直接猜根因。
2. 不扩大修改范围。
3. 不把设计问题当代码 Bug 强修。
4. 每个修复都要沉淀回归测试或问题记录。

### 12.4 自动运维

适用场景：

- 部署验证。
- 每日健康检查。
- 回归测试。
- 日志分析。
- 环境重建。

流程：

```text
触发任务
  -> 环境检查
  -> 运行脚本
  -> 收集日志
  -> AI 归因
  -> 低风险修复或建议
  -> 输出报告
```

自动运维的安全边界：

1. 不直接操作生产。
2. 不删除数据。
3. 不修改安全策略。
4. 不处理敏感日志。
5. 高风险动作只输出建议。

## 13. 第十步：建立白+黑模式

白+黑模式的本质是把“问题发现”和“问题修复”分时分工：

| 阶段 | 主体 | 任务 |
| --- | --- | --- |
| 白天 | 人工 | 真实使用、探索问题、记录 issue |
| 夜间 | Claude | 读取 issue、复现、归因、最小修复、回归 |
| 次日 | 人工 | 验收、决策、继续探索 |

### 13.1 白天 issue 模板

```markdown
## YYYY-MM-DD 问题记录

### P0 阻断
- [ ] 编号：P0-001
  现象：
  复现步骤：
  实际结果：
  预期结果：
  环境：
  复现率：
  日志：
  相关模块：
  验证命令：
  限制：

### P1 影响体验
- [ ] ...

### P2 细节
- [ ] ...
```

### 13.2 夜间启动 Prompt

```text
请启动白+黑夜间修复。
读取今天的 issue 文件：[路径]

规则：
1. 按 P0 -> P1 -> P2 排序。
2. 每条 issue 先复现，不可复现则标记待补充信息。
3. 可复现后做根因分析。
4. 只做最小修复，不引入新功能。
5. 每个修复补回归测试或验证记录。
6. 运行约定验证命令。
7. 最后输出次日交接摘要。
8. 遇到设计决策、生产权限、敏感数据、连续失败时停止该 issue。
```

### 13.3 次日交接摘要

```markdown
# 夜间修复摘要

## 已修复
| Issue | 根因 | 修改 | 验证 |

## 待人工确认
| Issue | 原因 | 需要谁决策 |

## 未处理
| Issue | 原因 | 下一步 |

## 新增回归
| 用例 | 覆盖问题 |

## 风险
- ...
```

## 14. 第十一步：安全和权限控制

AI 友好型编程不是降低安全要求，而是把安全要求写清楚，让 Claude 不越界。

### 14.1 权限控制

建议在 Claude Code 中检查：

```text
/permissions
```

团队可以按风险分层：

| 等级 | 允许行为 |
| --- | --- |
| L1 只读 | 读代码、读文档、做计划 |
| L2 本地编辑 | 修改代码和文档，但不执行外部破坏性命令 |
| L3 本地验证 | 运行测试、构建、lint、非生产脚本 |
| L4 测试环境操作 | 操作测试环境部署和回归 |
| L5 生产相关 | 必须人工审批，Claude 只给建议 |

### 14.2 禁止清单

写进 `CLAUDE.md`：

```markdown
## Forbidden Without Explicit Approval
- 删除大量文件。
- 执行数据库 DROP / TRUNCATE / DELETE 大范围语句。
- 修改生产配置。
- 输出密钥、token、cookie、客户数据。
- 绕过测试或安全扫描。
- 使用 --force、--no-verify、--skip-tests。
- 将临时调试代码提交为正式实现。
```

### 14.3 分支策略

推荐：

```text
main
  -> feature/<name>
  -> bugfix/<issue-id>
  -> nightly/<date>
```

夜间修复最好使用独立分支或 worktree。AI 修复失败时，不影响白天人工继续测试。

## 15. 第十二步：度量效果

搭建流程后，要用数据证明它有效。

### 15.1 过程指标

| 指标 | 说明 |
| --- | --- |
| AI 参与需求数 | 有多少需求走了 Spec + TDD |
| 测试前置比例 | 有多少实现前先定义测试 |
| 自动验证次数 | Claude 修改后运行了多少次测试 |
| 夜间处理 issue 数 | 白+黑一晚处理多少问题 |
| 待人工决策数 | Claude 正确停止并上交的问题 |
| 记忆沉淀数 | 新增多少决策、坑点、Prompt、Skill |

### 15.2 结果指标

| 指标 | 说明 |
| --- | --- |
| 需求交付周期 | 从需求输入到测试通过的时间 |
| Bug 平均定位时间 | 从问题记录到根因定位的时间 |
| 回归缺陷率 | 修复后是否反复出现同类问题 |
| 测试覆盖率 | UT / 集成 / 黑盒覆盖变化 |
| 人力节省 | 与传统开发估算对比 |
| 新人上手时间 | 新成员理解项目所需时间 |

### 15.3 成熟度分级

| 等级 | 状态 |
| --- | --- |
| L0 | 只用 Claude 临时写代码，无流程 |
| L1 | 有 `CLAUDE.md`，Claude 知道项目基本规则 |
| L2 | 有上下文资产和测试规约，新需求可按流程走 |
| L3 | 有 Skills 和 Commands，Bug 修复、TDD、根因分析可复用 |
| L4 | 有白+黑和自动运维，能夜间批量处理结构化 issue |
| L5 | 形成跨项目模板，可迁移到不同团队和受控环境 |

## 16. 从 0 到可用的落地顺序

不要一次性搭完所有东西。推荐按四周节奏推进。

### 第 1 周：让 Claude 读懂项目

目标：

1. 安装并检查 Claude Code。
2. 创建 `CLAUDE.md`。
3. 输出 `docs/ai/context-loading.md`。
4. 梳理构建、测试、启动命令。

验收：

```text
新开一个 Claude 会话后，Claude 能准确回答：
- 项目有哪些模块。
- 修改某类功能应该看哪些文件。
- 如何构建和测试。
- 哪些文件不能随意改。
```

### 第 2 周：让 Claude 按流程开发

目标：

1. 创建 `docs/ai/ai-dev-workflow.md`。
2. 创建 `docs/testing/test-as-spec.md`。
3. 用一个小需求跑通 TDD。
4. 用一个小 Bug 跑通根因分析。

验收：

```text
Claude 不再直接写代码，而是先输出 Spec、测试和边界。
```

### 第 3 周：沉淀 Skills 和 Commands

目标：

1. 创建 `tdd-spec` Skill。
2. 创建 `root-cause-analysis` Skill。
3. 创建 `/new-feature` 和 `/bugfix` 命令。
4. 把一次真实修复沉淀到 `memory/`。

验收：

```text
团队成员可以用固定命令启动同一套流程，结果格式稳定。
```

### 第 4 周：建立白+黑和自动运维

目标：

1. 创建 issue 模板。
2. 创建 `white-black-night-fix` Skill。
3. 创建 `/night-run` 命令。
4. 跑一次夜间修复演练。
5. 输出次日交接摘要。

验收：

```text
Claude 能在明确边界下连续处理多个结构化 issue，并知道何时停止。
```

## 17. 常见问题

### 17.1 是不是 Skill 越多越好

不是。Skill 应该服务高频、稳定、可复用的流程。临时任务用普通 Prompt 即可。Skill 过多会造成触发混乱和维护成本。

### 17.2 `CLAUDE.md` 和 Skill 重复怎么办

`CLAUDE.md` 写硬规则，Skill 写专项流程。例如“Bug 修复必须先复现”可以写在 `CLAUDE.md`；具体如何沿调用链做根因分析写在 `root-cause-analysis` Skill。

### 17.3 没有测试能不能做 AI 友好型编程

能，但第一阶段必须先补最小验证命令。没有验证，Claude 的修改只能算“看起来合理”，不能算工程闭环。

### 17.4 白+黑是不是让 AI 完全自主修改

不是。白+黑成立的前提是 issue 结构化、验证命令明确、修改边界明确、停止条件明确。AI 只处理可验证、低风险、边界清楚的问题；设计决策和高风险动作交还人工。

### 17.5 如何避免 AI 改错代码

核心手段不是更长 Prompt，而是：

1. 最小上下文闭包。
2. 测试前置。
3. 根因分析协议。
4. 修改边界。
5. 自动验证。
6. 长期记忆。

## 18. 一套可直接复制的启动 Prompt

首次把普通项目改造成 AI 友好型项目时，可以对 Claude 这样说：

```text
我想把当前项目改造成 AI 友好型编程项目。

请先不要改业务代码。
请分阶段完成：

第一阶段：项目体检
1. 阅读仓库结构。
2. 输出模块职责、启动命令、测试命令、高风险文件。
3. 生成 docs/ai/context-loading.md 草稿。

第二阶段：项目约束
1. 基于项目实际情况生成 CLAUDE.md 草稿。
2. 写清楚编码规则、测试规则、安全边界、常用命令。

第三阶段：流程固化
1. 生成 docs/ai/ai-dev-workflow.md。
2. 覆盖新需求、Bug 修复、重构、自动运维。

第四阶段：测试前置
1. 生成 docs/testing/test-as-spec.md。
2. 定义正常、异常、安全负向、依赖故障、回归测试。

第五阶段：Skills 和 Commands
1. 设计 .claude/skills/ 下的 Skill 清单。
2. 设计 .claude/commands/ 下的常用命令。
3. 先只生成最小可用版本。

每个阶段完成后先停下来，说明产物、风险和需要我确认的问题。
```

## 19. 和本项目的对应关系

本比赛作品中的 AIDP IAM / Gateway 实践，可以理解为上述搭建方法的一个完整样板：

| 通用搭建项 | 本项目中的对应材料 |
| --- | --- |
| 项目级流程规则 | `detail/AI友好型软件工程固化流程.md` |
| Claude / Agent 主流程样例 | `detail/引用材料/ai-dev-workflow.md` |
| 测试前置说明 | `detail/创新点-测试即规约与TDD.md` |
| 上下文和记忆说明 | `detail/创新点-上下文工程与长短期记忆.md` |
| 白+黑夜间模式 | `detail/创新点-白加黑协作模式.md` |
| 自动运维闭环 | `detail/自动运维与根因分析闭环.md` |
| 交互证据 | `../Agent交互记录.md` |
| 实际源码和脚本 | `../代码/` |

因此，本项目提交的不只是一个 IAM / Gateway 系统，也是一套“普通工程如何逐步变成 AI 友好型工程”的可复用搭建路线。

## 20. 参考资料

- Claude Code Memory：`CLAUDE.md`、项目记忆、用户记忆和 `/memory` 机制。参考官方文档：<https://docs.anthropic.com/en/docs/claude-code/memory>
- Claude Code Slash Commands：项目级和个人级自定义命令。参考官方文档：<https://docs.anthropic.com/en/docs/claude-code/slash-commands>
- Claude Code Agent Skills：项目 Skills、个人 Skills、`SKILL.md` 和自动发现机制。参考官方文档：<https://docs.claude.com/en/docs/claude-code/skills>
