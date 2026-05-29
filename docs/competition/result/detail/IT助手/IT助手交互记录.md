IT助手交互记录；
# IT-Assistant-Web 功能说明书

## 1. 项目概述

| 属性 | 说明 |
|-----|------|
| **项目名称** | 辅助单元测试代码生成系统 |
| **技术栈** | Python3.12.10 + nodejs 24.15.0 + 原生HTML/CSS/JS |
| **核心功能** | 网页上对编译环境进行管理，同时通过页面内嵌的CodeAgent进行交互，让AI生成UT代码 |

### 1.1 业务背景

当前黄区补充UT代码费时费力，很多都是重复性的工作。开发人员使用AI在本地生成UT之后还需要上传到环境上跑覆盖率，自动化程度低，浪费时间。因此需要实现一款能够让AI半自动进行迭代提升覆盖率的工具。

---

## 2. 覆盖率提升效果

### 2.1 初始化覆盖率

以下是可补充测试的低覆盖率源文件（按覆盖率从低到高排序）：

| 源文件 | 行覆盖率 | 函数覆盖率 | 现有测试 |
|-------|---------|-----------|---------|
| iod_diagnose.c | 0.0% | 0.0% | ❌ 无 |
| iod_load_sched.c | 0.0% | 0.0% | ❌ 无 |
| iod_load_sched_monitor.c | 0.0% | 0.0% | ❌ 无 |
| iod_ver_comm.c | 0.0% | 0.0% | ❌ 无 |
| iod_core.c | 0.0% | 0.0% | ❌ 无 |
| iod_dispatch_alg.c | 0.0% | 0.0% | ❌ 无 |
| iod_qos.c | 0.0% | 0.0% | ❌ 无 |
| iod_timer.c | 6.5% | 7.7% | ❌ 无 |
| iod_req.c | 4.0% | 7.3% | ✅ iod_req_tests.cc |
| iod_schedule.c | 33.6% | 32.0% | ❌ 无 |

### 2.2 3轮补充后的覆盖率

**补充了5个函数之后的进度总结：**

- **测试文件**：`testcase/iod/iod_req_delay_ut.cc`
- **新增测试用例**（全部通过）：
  - getReqThreadId - 获取请求线程ID
  - getReqThreadNum - 获取请求线程数
  - initReqIodPrivate - 初始化请求私有数据
  - addDelayStartQ - 添加延迟启动队列
  - addStartQInHead - 头部添加启动队列
  - addDelayStartQByPartitionId - 按分区ID添加延迟启动队列

**覆盖率变化：**
| 指标 | 之前 | 之后 | 提升 |
|-----|------|------|------|
| 行覆盖率 | 23.5% | 24.5% | +1.0% |
| 函数覆盖率 | 27.3% | 29.1% | +1.8% |

**修复的问题：**
- 发现 `INVALID_PID = 0`，有效的 pid 范围是 1-1023
- 修复了测试中 `pid=0` 导致 `checkReqValid` 失败的断言问题

**迭代后的最终结果：**
| 指标 | 之前 | 之后 | 提升 |
|-----|------|------|------|
| 行覆盖率 | 24.5% | 56.9% | +32.4% |
| 函数覆盖率 | 29.1% | 61.8% | +32.7% |

---

## 3. 核心修改记录

### 3.1 系统功能修改

| 原功能 | 修改后功能 |
|-------|----------|
| 页面: "环境管理" | "环境录入" + CodeAgent CLI交互终端 |
| 环境管理: "添加环境" | "添加环境" + "测试连接" |

### 3.2 数据传入CodeAgent

通过环境变量传递以下信息，让网页中启动的CodeAgent感知环境上下文：

- **环境变量传递**：增加环境变量，让CodeAgent感知传入的环境信息
- **项目路径传递**：增加项目路径，让CodeAgent感知项目上下文
- **调试经验**：数据传入CodeAgent通过环境变量传递，如果AI无法获取，提示AI校验方法是否有效，让AI自己排查代码后解决问题

**CodeAgent实际运行效果：**
```
Thinking: 用户想要查看test1环境信息。我需要搜索一下代码库中与test1环境相关的配置或信息。
✱ Grep "test1" (1 match)
✱ Glob "**/test1*" 
Thinking: 找到了envs.json文件，让我读取该文件来查看test1环境信息。
→ Read llt-assistant-web\server\envs.json 
```

**test1环境信息输出：**
| 属性 | 值 |
|-----|-----|
| 名称 | test1 |
| IP | [已脱敏IP] |
| 端口 | 30889 |
| 用户名 | [已脱敏用户] |
| 密码 | [已脱敏密码] |
| 工作目录 | /home/workspace |
| 项目路径 | [已脱敏项目路径] |

### 3.3 环境信息持久化

- **添加环境**：将环境信息持久化到本地文件中
- **编辑环境**：将环境信息持久化到本地文件中

### 3.4 UI优化

- **终端显示优化**：CodeAgent CLI交互终端开始显示排布混乱 → 优化为可以宽屏显示

### 3.5 Skills技能增强

- 通过AI的3轮覆盖率补充和bug修复，将经验总结成skills，方便AI补充UT的效率
- 补充覆盖率中，遇到一些覆盖率很低但函数依赖外部模块、未导出的情况，让AI总结成skills，优先补充好补的文件覆盖率

---

## 4. 项目结构

```
llt-assistant-web/
├── public/
│   ├── css/
│   │   └── style.css          # 前端样式
│   ├── index.html             # 主页面
│   └── js/
│       ├── app.js             # 环境管理逻辑
│       └── terminal.js        # 终端交互逻辑
├── server/
│   ├── envs.json              # 环境配置存储
│   └── server.js              # 后端服务
├── package-lock.json
└── package.json
```

**目录说明：**
| 目录 | 说明 |
|-----|------|
| `public/` | 前端静态资源 |
| `server/` | 后端服务 |
| `envs.json` | 环境配置存储文件 |

---

## 5. 运行命令

```bash
# 编译并运行
npm start

# 访问地址
# 首页: http://localhost:3002/
```

---

## 6. API端点

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/` | 获取前端页面 | - | HTML页面 |
| POST | `/api/connect` | 连接CodeAgent CLI | 环境配置对象 | `{success, message}` |
| POST | `/api/disconnect` | 断开CodeAgent连接 | - | `{success, message}` |
| POST | `/api/test-connection` | 测试SSH连接 | `{ip, port, username, password, workdir}` | `{success, message, envInfo}` |
| GET | `/api/env-list` | 获取环境列表 | - | `{success, envs: [...]}` |
| POST | `/api/env-list` | 保存环境列表 | 环境数组`[...]` | `{success, message}` |
| POST | `/api/save-env` | 保存当前环境 | 环境对象 | `{success, message}` |
| GET | `/api/get-current-env` | 获取当前环境 | - | `{success, env}` |
| GET | `/api/status` | 获取连接状态 | - | `{connected, running, env}` |

---

## 7. 业务规则

### 7.1 添加环境

将代码构建环境的信息、项目路径录入系统中，支持：
- 环境名称管理
- 服务器IP、端口配置
- 用户名密码认证
- 工作目录设置
- 项目路径配置

### 7.2 覆盖率查看

通过查看覆盖率的技能，使用CodeAgent查看当前项目的覆盖率情况，确定文件难易程度，便于制定补充策略。

### 7.3 自动补充UT

告知CodeAgent提高特定文件覆盖率，使用UT技能让CodeAgent能根据已有的UT规范来补充覆盖率：

1. **技能规范**：通过skills约束AI行为，降低犯错频率
2. **迭代优化**：AI自动进行覆盖率补充、迭代、问题识别
3. **结果验证**：生成的代码符合团队编码规范，可通过静态扫描

