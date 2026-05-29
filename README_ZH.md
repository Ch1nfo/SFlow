<div align="center">

# SentinelFlow

### AI-Native SecOps 控制平面 — 多 Agent SOC 自动化平台

[![版本](https://img.shields.io/badge/版本-1.3.0-blue.svg)](https://github.com/Ch1nfo/SentinelFlow/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![许可证](https://img.shields.io/badge/许可证-MIT-green.svg)](LICENSE)
[![平台](https://img.shields.io/badge/平台-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#)
[![基于 LangGraph](https://img.shields.io/badge/基于-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)

[English](README.md) | 中文

</div>

---

## 为什么选择 SentinelFlow？

现代安全运营中心（SOC）每天要面对海量告警——大多数团队需要花费数小时进行研判，而这些工作本可以在秒级完成。现有的 SIEM 平台提供基于规则的关联分析，但缺乏处理新型威胁或复杂多步骤调查所需的上下文推理能力。

**SentinelFlow** 是一个全栈 SOC 自动化平台，将 **基于 LangGraph 的多 Agent 编排运行时**与**面向运营协同的 React WebUI** 深度结合。不同于固化的剧本，你将拥有一套灵活、可扩展的 Agent 体系——主 Agent（Supervisor）统一调度各专项子 Agent（Worker），每个子 Agent 均可装配可热插拔的 Skill，实现外部 API 调用、情报富化脚本、工单闭合等任意安全运营动作。

- **多 Agent 编排** — 基于 LangGraph 的 Supervisor + Worker SubGraph 模式，支持顺序/并行委派以及由主 Agent 引导执行的 Workflow
- **完整运营控制台** — 统一 WebUI 覆盖总览、告警工作台、任务中心、对话指挥台、Skills、RAG、Agents、Workflows、平台设置和运行日志查看
- **可插拔 Skill 系统** — 在本地插件工作区中放入 `SKILL.md` + `main.py` 即可，Agent 自动发现并调用，支持细粒度的按 Agent 权限控制
- **双入口处理** — 同时接受原始安全告警（SIEM/SOAR 的 JSON 告警）和 WebUI 对话台中的自由文本人工指令
- **Agent Workflow 引擎** — 定义可复用的多步骤工作流；主 Agent 读取固定计划后，仍负责为每一步生成具体任务并调用对应子 Agent
- **多告警源接入** — 支持配置多个命名告警源，每个源可使用 HTTP API 轮询或 Python 脚本入口，并拥有独立解析规则与调度参数
- **AI 辅助解析规则生成** — 粘贴告警样本，大模型自动生成字段映射解析规则，并支持预览后保存
- **按源异步自动处置与失败重试** — 可按告警源开启后台自动执行，异步处理排队告警，并按各源配置的延迟自动重试失败任务
- **审批与断点恢复** — `approval_required` Skill 在手动图执行中会暂停流程，在 UI 中显示审批卡，并在批准/拒绝后从 checkpoint 恢复
- **单次任务上下文窗口控制** — 每次 LLM 调用前生成受预算约束的 `llm_prompt_view`，保留任务锚点、`case_context`、最近 ReAct 轮次和压缩工具结果，完整 state/checkpoint/run log 不丢失
- **SOC 执行护栏** — 通过权威事实、关键字段、任务锚点识别和执行前参数校验，让 Agent 更准确识别 IP、收信人、告警 ID 和结单状态
- **思考模型适配开关** — 在平台设置中可按需启用 DeepSeek 等 thinking 模型所需的 `thinking: disabled` 请求体
- **细粒度治理策略** — 按 Agent 配置 Skill 权限、按模式分离 Worker 委派权限、执行审批门控、审计日志和 Agent 级模型覆盖

## v1.3.0 更新重点

- **大体量告警下的数据展示稳定性** — 告警列表、总览计数、本周摘要和新告警提示改为走轻量行查询或专用 SQL 聚合，而不是拉取整批任务大字段，因此在大型 SQLite 队列下 WebUI 仍可保持流畅。
- **更可靠的实时数据加载** — Poll/Resource Store 增加后台刷新、强制刷新合并和更稳妥的缓存复用，告警、总览、Skills、Agents 页面在高频轮询下更平稳。
- **纯文本 Skill 调用增强** — `execute_skill` 现在可规范化纯文本 JSON 入参，并把授权 Skill 的 `input_schema` 提示直接暴露给模型，Skill 入参校验和审计也更完整。
- **真实结单执行校验** — 当子 Agent 被分派到需要处置或结单的任务时，必须实际调用授权结单 Skill；仅用文字描述或模拟 JSON 声称成功将不再被视为完成。
- **配置中心保存态更准确** — 设置页的脏状态判断已修正，日常编辑时不会再更容易误报“存在未保存改动”。

## 界面预览

|                        态势总览仪表盘                        |                        Agent 对话指挥台                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405225720016](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405225720053.png) | ![image-20260405231100920](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260406140803364.png) |

|                        告警工作台                        |                        Skill 管理面板                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405225903594](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405225903635.png)| ![image-20260405230107750](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230107788.png) |

|                        Agent 管理面板                         |                        Workflow 管理面板                        |
| :----------------------------------------------------------: | :----------------------------------------------------------: |
| ![image-20260405230145352](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230145399.png) | ![image-20260405230315299](https://raw.githubusercontent.com/Ch1nfo/picbed/main/img/20260405230315341.png) |

## 功能特性

### 多 Agent 编排

- **Supervisor + Worker SubGraph** — 主 Agent 通过 LangGraph 的 `ToolNode` 将任务委托给子 Agent，每个子 Agent 以独立 ReAct SubGraph 编译后封装为 `@tool`
- **并行委派** — 主 Agent 可通过 `delegate_parallel` 同时将多个独立子任务分发给不同 Worker 并行执行
- **Agent Workflow 引擎** — 定义可复用的固定多步骤工作流，用于高频场景；`run_workflow` 只加载固定计划，主 Agent 仍负责为每一步子 Agent 调用生成完整 task_prompt
- **SOC 上下文窗口管理器** — 每次 Supervisor/Worker 调用前由 `prepare_messages_for_llm()` 生成 prompt view：系统提示词、当前任务锚点、`case_context`、最近 ReAct 轮次和压缩后的旧工具结果
- **单次 run 内 case_context** — 在一次任务内维护 goal、alert refs、facts、actions taken、missing inputs、pending approvals、completed steps 和 do-not-repeat 等结构化工作台信息
- **按模式分离的 Worker 权限** — 主 Agent 在对话式执行与告警处置执行下可以使用不同的 Worker 白名单
- **提示词分层** — 主 Agent 支持基础 / 指令 / 告警 / 汇总四类提示词，子 Agent 使用统一执行提示词
- **取消与步骤上限** — 所有编排图均尊重 `cancel_event` 线程标志；`worker_max_steps` 限制编排递归深度，防止失控

### 可插拔 Skill 系统

- **基于 SKILL.md 的自动发现** — 每个 Skill 是一个目录，包含带 YAML 头部的 `SKILL.md`（供 Agent 阅读）和可选的 `main.py` 执行入口
- **两种 Skill 类型**：`doc`（纯知识型，供 Agent 阅读）和 `hybrid`（文档 + 可执行子进程）
- **按 Agent 权限控制** — `doc_skill_allowlist`、`exec_skill_allowlist`、每个 Skill 的 `approval_required` 标志；`approval_required` 仅对对话与手动单告警生效，且每次执行都需要单独审批，自动执行 / 自动重试 / 调试会绕过审批
- **Schema 感知的纯文本调用** — `execute_skill` 可规范化纯 JSON 字符串入参，将授权 Skill 的 `input_schema` 直接提示给模型，并在真正进入子进程前保留完整校验/审计信息
- **子进程隔离执行** — Skill 在隔离子进程中运行，结构化 JSON 输入/输出，内置审计日志
- **紧凑 LLM 数据面** — 大型工具输出和 Skill 文档保留在 runtime state/run log 中，重新进入 LLM 上下文前会先摘要化
- **WebUI 内 Skill 管理** — 直接在独立 Skills 页面创建、编辑、删除、查看并在线调试 Skill

### 告警接入流水线

- **多个命名告警源** — 在配置中心管理多个上游源；每个源都有独立名称、启用状态、解析规则、轮询间隔、重试间隔、自动执行开关和可选的源专属分析 Prompt
- **双模式告警源** — `api` 模式轮询任意 REST 端点（支持自定义 Method、Header、Query、Body）；`script` 模式运行自定义 Python 脚本，读取其 stdout 作为告警数据，适用于无 API 的自定义数据源
- **AI 辅助解析规则生成** — 粘贴原始告警样本，大模型自动生成 `field_mapping` 解析规则并实时预览；大模型不可用时自动降级为启发式规则推断
- **抓取 / 解析校验** — 保存前可直接测试上游抓取、导入解析规则 JSON，并预览解析后的标准化告警
- **灵活字段映射** — 基于点路径表达式，将任意 JSON 结构映射到 SentinelFlow 标准字段（`eventIds`、`alert_name`、`sip`、`dip`、`alert_time` 等）
- **按源去重与幂等** — SQLite 支撑的去重存储，防止活跃告警重复入队，同时隔离不同告警源中相同 event ID 的任务
- **按源轮询调度器** — 每个启用的告警源可按自己的间隔轮询；UI 支持对当前源立即触发轮询，API 也支持全部源轮询
- **大队列性能路径** — 告警队列改为轻量列表/标题查询、结果摘要字段反规范化、可索引 `sort_time` 排序，以及面向总览与本周统计的专用时间段聚合接口
- **容错与重试** — 失败任务可手动重试，也可按对应告警源配置的重试间隔自动重新处理

### 任务队列与执行

- **SQLite 按源任务队列持久化** — 默认将告警处置任务、告警源 ID、告警源名称与审批记录持久化到 `.sentinelflow/sys_queue.db`，进程重启后自动恢复
- **按源持续自动执行** — 可按告警源开启自动执行循环，无需人工干预即可异步处理排队任务
- **失败任务自动重试** — 可按告警源配置失败任务重试间隔，让后台循环自动拾取满足条件的失败任务再次处置
- **手动单任务触发** — 随时从告警工作台触发单条任务的 Agent 处置
- **完整任务生命周期** — `queued → running → awaiting_approval / pending_closure / succeeded / failed / completed`；手动审批可在不丢失断点状态的情况下暂停任务
- **结构化执行链路** — 每个任务存储完整 `execution_trace`，涵盖告警接收、Workflow 使用、Agent 研判、Skill 调用、审批状态、结单结果和最终状态
- **基于事实的结果收敛** — 最终状态、研判分类、处置结果、结单状态和 Workflow 使用情况会统一收敛到结构化 `final_facts`
- **闭环策略语义** — Skill 级 `completion_policy` 区分情报查询、遏制封禁、终态通知和正式结单，让任务状态以真实执行事实为准，而不是依赖松散文本描述
- **终态动作执行完整性** — 对需要处置/结单的委派任务，SentinelFlow 会校验子 Agent 是否真正产生了结单 Skill 的工具结果，而不接受仅用自然语言描述“已经执行”
- **运行日志可追溯** — LLM prompt view、窗口大小、Worker 边界、构造后的 Skill 入参、审批事件和最终任务结果均会进入运行日志，便于审计和排错

### 安全运营 WebUI

- **总览仪表盘** — 统一展示运行健康度、任务数量、Agent/Skill 可用性、研判分布、处置结果和最近平台动作
- **告警工作台** — 切换不同告警源，浏览按源隔离的任务队列，启动/停止自动执行，手动触发 Agent 处置，查看告警上下文、审批状态和完整执行链路
- **任务中心** — 以任务为中心查看队列状态、重试失败任务、处理待审批 Skill，并检查完整处置全流程
- **对话指挥台** — 自由文本指令界面，支持多会话、流式响应、可折叠子 Agent/Skill 摘要、执行上下文详情和内联审批卡片
- **配置中心** — 统一配置页面，涵盖 LLM 凭据、思考模型适配、多告警源连接、解析规则、轮询参数、失败重试间隔、运行日志保留策略、自动执行开关——所有配置实时持久化，无需重启服务
- **稳定的缓存式实时刷新** — 告警、总览、Skills、Agents 页面采用共享 Store、类似 stale-while-revalidate 的刷新策略、强制刷新排队和更少的重复请求，适合高频轮询场景
- **设置页脏状态保护** — 配置表单对“是否已改动”的判断更准确，减少正常编辑过程中的误报和不必要提示
- **RAG 设置** — 通过 WebUI 配置内置 RAG Skill、知识库 ID、检索参数、API Key、重排模型和 Agent 可用性
- **运行日志查看器** — 在平台设置的调试面板中查看单告警执行日志、prompt window、Worker/Skill 事件和入参来源
- **Skill 管理** — 创建、查看、编辑、删除 Skill；支持携带自定义参数进行在线调试执行
- **Agent 管理** — 配置主 Agent 和子 Agent：提示词（默认/告警/指令/汇总 四种变体）、LLM 参数覆盖、Skill 权限和按模式分离的 Worker 委派权限
- **Workflow 管理** — 创建、编辑 Agent Workflow；支持从 UI 直接发起测试运行

### 平台与架构

- **FastAPI 后端** — 异步 Python 运行时，结构化 JSON API，uvicorn 服务器
- **React + Vite 前端** — TypeScript、TailwindCSS、组件化架构
- **统一开发入口** — `python scripts/dev.py dev` 一条命令启动全部服务
- **源码优先的本地布局** — 运行时代码位于 `runtime/`，WebUI 位于 `webui/`，辅助脚本位于 `scripts/`，本地插件与运行时状态默认保存在项目根 `.sentinelflow/`

## 架构总览

<details>
<summary><strong>系统架构图</strong></summary>

```
┌─────────────────────────────────────────────────────────────────┐
│                   React WebUI (Vite + TS)                       │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────────┐   │
│  │ 总览 / 告警 / │  │  对话台 + 审批   │  │ 插件与配置管理中心 │   │
│  │   任务页面    │  │                  │  │                   │   │
│  └──────────────┘  └─────────────────┘  └───────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │  REST API（FastAPI）
┌──────────────────────────▼──────────────────────────────────────┐
│              SentinelFlow 运行时（Python / FastAPI）             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                   多 Agent 编排器                           │  │
│  │   ┌──────────────────────────────────────────────────┐    │  │
│  │   │  主 Agent（Supervisor）                           │    │  │
│  │   │  LangGraph StateGraph + ToolNode                  │    │  │
│  │   │  Context Window → ReAct → Worker/Skill Tools      │    │  │
│  │   │    ↓ 顺序 / 并行委派子 Agent                      │    │  │
│  │   │  ┌────────────┐  ┌────────────┐  ┌────────────┐  │    │  │
│  │   │  │  子 Agent A│  │  子 Agent B│  │  子 Agent C│  │    │  │
│  │   │  │ ReAct Sub- │  │ ReAct Sub- │  │ ReAct Sub- │  │    │  │
│  │   │  │   Graph    │  │   Graph    │  │   Graph    │  │    │  │
│  │   │  └────────────┘  └────────────┘  └────────────┘  │    │  │
│  │   └──────────────────────────────────────────────────┘    │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                      Skill 运行时                           │  │
│  │   loader → executor → 子进程隔离 → 审计日志                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              Prompt Window & 运行日志追踪                   │  │
│  │  任务锚点 → case_context → compact tool records             │  │
│  │  完整 state / checkpoint / run log 保持可追溯               │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              告警接入 & SQLite 任务队列                      │  │
│  │  多源 API/脚本轮询 → 解析器 → 去重 → SQLite 任务队列          │  │
│  │  按源自动执行循环 → 任务执行器 → Agent / Workflow             │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**核心设计模式**

- **Supervisor + Worker SubGraph** — 子 Agent 以编译后的 ReAct SubGraph 形式封装为 `@tool`，以紧凑摘要、关键事实、动作结果、审批状态和错误信息返回给主 Agent
- **SOC 上下文窗口控制** — `prepare_messages_for_llm()` 从完整 state 生成实际送入 LLM 的 prompt view，保留任务锚点和最近 ReAct 轮次，压缩较早工具记录
- **单次任务 case_context** — `case_context` 在当前 run 内携带当前目标、告警引用、关键事实、已执行动作、缺失输入、待审批项、已完成步骤和防重复提示
- **SKILL.md 自动发现** — Skill 是文件系统插件；无需修改代码即可添加新能力
- **双入口类型** — `alert`（来自 SIEM 的 JSON 告警）和 `conversation`（人工指令）；均通过同一 Agent 运行时路由
- **按源 SQLite 任务持久化** — 告警任务跨进程重启持久化；按源隔离 event ID，并通过原子状态转换防止重复执行
- **原子化结果序列化** — 所有执行结果均经 `_serialize_alert_result` 统一处理，产出结构一致的执行链路追踪
- **完整审计 / 紧凑推理分离** — 完整 state、checkpoint 与运行日志保留；只有实际送入 LLM 的 prompt 会做窗口化和压缩

**核心组件**

- **`SentinelFlowAgentService`** — 顶层服务，负责路由到编排器或单 Agent 图，并序列化执行结果
- **`build_orchestrator_graph()`** — 编译 Supervisor + Worker 多 Agent LangGraph
- **`build_agent_graph()`** — 构建单 Agent ReAct SubGraph（同时用于子 Agent 和独立 Agent）
- **`context_utils`** — 构建 context manifest、任务锚点、case context、prompt window、关键事实、紧凑工具摘要和执行前输入校验
- **`RunLogTracer` / `AgentRunLogService`** — 记录每次运行的 LLM prompt view、窗口信息、Worker 边界、Skill 调用、审批事件和最终结果
- **`AlertDispatchService`** — SQLite 支撑的按源任务队列；负责任务创建、去重、状态转换和闭合
- **`AlertPollingService`** — 按源调度启用中的 API/脚本告警源，并将标准化告警写入任务队列
- **`AlertAutoExecutionService`** — 基于 asyncio 的按源持续自动执行循环，无需人工干预处理排队与可重试任务
- **`AlertParserGenerator`** — 大模型辅助 + 启发式 JSON 告警字段映射规则生成器
- **`SentinelFlowSkillRuntime`** — 管理 Skill 生命周期，将 Skill 适配为 LangChain 工具供 Agent 使用
- **`AgentWorkflowRegistry`** — 列举和解析固定多步骤 Agent Workflow 定义
- **`SkillApprovalService`** — 持久化审批记录与断点恢复状态，用于人机协同执行
- **`weekly_alert_cleanup_service`** — 可选的每周告警与运行数据清理服务
- **`AuditService`** — 记录分发、任务执行、审批处理和后台服务相关审计事件

</details>

<details>
<summary><strong>项目结构</strong></summary>

```
.
├── pyproject.toml                      # Python 包元数据与 CLI 入口
├── scripts/
│   ├── dev.py                          # 统一本地开发入口
│   └── serve_webui.py                  # 生产环境 WebUI 静态文件服务
├── .sentinelflow/                      # 本地插件、runtime.json、SQLite 队列（运行时生成）
├── runtime/
│   └── sentinelflow/
│       ├── agent/
│       │   ├── service.py              # 顶层 Agent 服务（编排核心逻辑）
│       │   ├── orchestrator_graph.py   # Supervisor + Worker SubGraph 构建器
│       │   ├── graph.py                # 单 Agent ReAct 图构建器
│       │   ├── registry.py             # Agent 定义加载器（agent.yaml）
│       │   ├── prompts.py              # 系统提示词与附录模板
│       │   ├── context_utils.py        # context manifest、prompt window、case_context、紧凑记录
│       │   ├── run_log_tracer.py       # LLM prompt / 运行日志事件追踪
│       │   ├── skill_run_analyzer.py   # Skill / 结单 / 动作结果收敛
│       │   ├── policy.py               # 按 Agent Skill 权限解析器
│       │   ├── nodes.py                # LangGraph 节点实现
│       │   ├── tools.py                # Agent 侧工具定义
│       │   └── state.py                # Agent 图状态 Schema
│       ├── skills/
│       │   ├── loader.py               # SKILL.md 发现与校验
│       │   ├── executor.py             # Skill 子进程执行器
│       │   ├── adapters.py             # Skill → LangChain 工具适配器
│       │   ├── resolver.py             # 本地 / 插件 Skill 解析
│       │   └── models.py               # Skill 数据模型
│       ├── alerts/
│       │   ├── client.py               # 告警源 HTTP / 脚本客户端
│       │   ├── poller.py               # 定时轮询服务
│       │   ├── parser_runtime.py       # 字段映射解析引擎
│       │   ├── parser_generator.py     # 大模型 + 启发式解析规则生成器
│       │   └── dedup.py                # 告警去重存储
│       ├── services/
│       │   ├── agent_run_log_service.py # 按告警记录 JSONL 运行日志
│       │   ├── dispatch_service.py     # SQLite 任务队列与生命周期管理
│       │   ├── task_runner_service.py  # 任务执行编排
│       │   ├── auto_execution_service.py # 持续自动执行循环
│       │   ├── skill_approval_service.py # Skill 审批记录与 checkpoint 持久化
│       │   ├── triage_service.py       # 基于规则的处置结论兜底
│       │   ├── weekly_alert_cleanup_service.py # 可选每周清理
│       │   └── audit_service.py        # 审计事件日志
│       ├── tools/                      # 内置运维工具
│       ├── workflows/                  # Agent Workflow 注册表与执行器
│       ├── api/                        # FastAPI 路由处理器
│       ├── config/                     # 运行时配置加载器（.env + 持久化 JSON）
│       └── domain/                     # 共享枚举、模型、错误类型
│   └── tests/                          # Runtime 回归测试
├── webui/
│   └── src/
│       ├── components/                 # React UI 组件
│       ├── pages/                      # 页面级视图
│       ├── api/                        # API 客户端（fetch 封装）
│       ├── hooks/                      # 自定义 React Hooks
│       └── styles/                     # 全局样式与 Tailwind 配置
```

</details>

<details>
<summary><strong>开发指南</strong></summary>

### 环境要求

- Python 3.11+
- Node.js 18+ / pnpm 8+
- （可选）兼容 OpenAI ChatCompletions 格式的 LLM API Key

### 开发命令

```bash
# 克隆并初始化 Python 环境
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 安装 WebUI 依赖
cd webui && pnpm install && cd ..

# 启动完整开发环境（后端 + 前端）
python scripts/dev.py dev

# 仅启动后端
python scripts/dev.py backend

# 仅启动 WebUI 开发服务器
python scripts/dev.py webui-dev

# 构建生产版 WebUI
python scripts/dev.py webui-build

# 启动构建后的 WebUI 静态服务
python scripts/dev.py webui-serve
```

通过 editable install 安装后，也可直接使用 CLI：

```bash
sentinelflow dev
sentinelflow backend
```

### 配置方式

**推荐方式**：直接在 WebUI 的 **配置中心** 填写配置，所有参数默认实时持久化到 `.sentinelflow/runtime.json`，无需重启服务。

**备选方式**：在项目根目录创建 `.env` 并填写环境变量（所有参数均以 `SENTINELFLOW_` 为前缀）：

```bash
touch .env
```

核心环境变量：

```ini
# LLM 配置（OpenAI 兼容格式）
SENTINELFLOW_LLM_API_KEY=sk-...
SENTINELFLOW_LLM_API_BASE_URL=https://api.openai.com/v1
SENTINELFLOW_LLM_MODEL=gpt-4o
SENTINELFLOW_LLM_THINKING_ADAPTER_ENABLED=false  # 仅在 DeepSeek 等 thinking 模型适配时开启

# 告警源
SENTINELFLOW_ALERT_SOURCE_ENABLED=false
SENTINELFLOW_ALERT_SOURCE_TYPE=api          # "api" 或 "script"
SENTINELFLOW_ALERT_SOURCE_URL=https://your-siem/api/alerts
SENTINELFLOW_POLL_INTERVAL_SECONDS=60

# 自动执行
SENTINELFLOW_AUTO_EXECUTE_ENABLED=false

# 运行时
SENTINELFLOW_AGENT_ENABLED=true
SENTINELFLOW_RUN_LOG_RETENTION_DAYS=1
SENTINELFLOW_WEEKLY_ALERT_CLEANUP_ENABLED=false

# RAG Skill 默认配置
SENTINELFLOW_RAG_ENABLED=true
SENTINELFLOW_RAG_KNOWLEDGE_ID=your-knowledge-id
SENTINELFLOW_RAG_API_KEY=sk-...
```

### 技术栈

**后端**：Python 3.11 · FastAPI · uvicorn · LangGraph · LangChain · Pydantic v2 · python-dotenv · SQLite

**前端**：React 19 · TypeScript · Vite 7 · TailwindCSS · React Router

**AI 运行时**：LangGraph（StateGraph + ToolNode）· LangChain Core · langchain-openai

</details>

## 快速开始

### 1. 安装 Python 依赖

```bash
# Linux/Mac
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Windows CMD
python -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[dev]"
```

### 2. 安装 WebUI 依赖

```bash
cd webui
pnpm install
cd ..
```

### 3. 启动全栈开发环境

```bash
python scripts/dev.py dev
```

默认启动：
- **后端 API**：`http://127.0.0.1:8001`
- **WebUI**：`http://127.0.0.1:5173`

如果想本地模拟生产态预览，可先构建前端再启动静态服务：

```bash
python scripts/dev.py webui-build
python scripts/dev.py webui-serve
```

### 4. 通过 WebUI 完成配置

打开 WebUI，进入 **配置中心**，配置 LLM 接入地址和一个或多个告警源连接参数——所有配置实时生效，无需重启服务。

也可以通过 `.env` 文件设置环境变量作为默认值：

```bash
touch .env
# 编辑 .env，填写 SENTINELFLOW_LLM_API_KEY、SENTINELFLOW_LLM_API_BASE_URL 等
```

### 5. 添加你的第一个 Skill（可选）

在 `.sentinelflow/plugins/skills/`（默认本地工作区）下创建目录并添加 `SKILL.md`，或直接在 WebUI 的 **Skill 管理** 页面中在线创建：

```markdown
---
name: get-ip-info
description: 查询指定 IP 的地理位置与威胁情报
type: hybrid
mode: subprocess
entry: main.py
execute_policy:
  enabled: true
  approval_required: false
  audit: true
---

# get-ip-info

通过外部威胁情报 API 查询 IP 信誉、ASN 和地理位置信息。

## 输入

- `ip`：待查询的 IP 地址

## 输出

返回包含 `country`、`asn`、`reputation`、`is_malicious` 字段的 JSON 对象。
```

Agent 将自动发现并在适当时调用此 Skill。

`approval_required` 仅对 **Agent 对话** 与 **手动单告警 / 手动重试** 两类入口生效；在这两类入口下，每次实际执行都需要重新审批。开启自动执行时，即使配置了 `approval_required: true`，Skill 也会直接执行。

## 常见问题

<details>
<summary><strong>SentinelFlow 支持哪些 LLM 服务商？</strong></summary>

SentinelFlow 使用 OpenAI 兼容 API 接口（`langchain-openai`）。所有支持 OpenAI Chat Completions API 格式的服务商均可接入，包括 OpenAI、Anthropic（通过代理）、DeepSeek、通义千问、本地模型（Ollama/LM Studio）以及各类 API 中转服务。

在 WebUI 配置中心填写，或通过环境变量配置：
```ini
SENTINELFLOW_LLM_API_BASE_URL=https://your-provider/v1
SENTINELFLOW_LLM_API_KEY=your-key
SENTINELFLOW_LLM_MODEL=model-name
```

对于 DeepSeek 这类 thinking 模型，可在平台设置中开启 **思考模型适配**，或设置 `SENTINELFLOW_LLM_THINKING_ADAPTER_ENABLED=true`。开启后 SentinelFlow 会发送 `thinking: {"type": "disabled"}`，避免 provider 要求回传 `reasoning_content` 的报错。普通不支持该字段的模型应保持关闭。

</details>

<details>
<summary><strong>支持哪些告警源接入方式？</strong></summary>

SentinelFlow 支持在配置中心管理多个命名告警源。每个告警源都可以选择以下两种模式之一：

- **API 模式**（`api`）：轮询任意 REST/HTTP 端点，支持 GET/POST，可自定义 Header、Query 参数、请求体，适用于提供 REST API 的 SIEM/SOAR 平台。
- **脚本模式**（`script`）：直接在 UI 中编写 Python 脚本，脚本将告警数据以 JSON 格式打印到 stdout（需包含 `count` 和 `alerts` 字段）。适用于无 REST API 的自定义数据源、本地日志文件或任何特殊集成场景。

每个源都有自己的解析规则、轮询间隔、失败重试间隔、自动执行开关和可选告警分析 Prompt。任务会保存 `source_id` / `source_name`，去重也按“告警源 + event ID”隔离。

</details>

<details>
<summary><strong>AI 解析规则生成是如何工作的？</strong></summary>

在配置中心粘贴一段原始告警 JSON 样本，点击 **生成解析规则**。SentinelFlow 将样本发送给你配置的大模型，模型返回一份 `field_mapping` 规则，将你的字段映射到 SentinelFlow 标准字段（`eventIds`、`alert_name`、`sip`、`dip` 等）。实时预览展示规则对样本的解析效果。若大模型调用失败或未配置，将自动降级为基于样本结构的启发式规则推断。

</details>

<details>
<summary><strong>如何定义一个子 Agent（Worker）？</strong></summary>

在 `.sentinelflow/plugins/agents/`（默认本地工作区）下创建目录，包含 `agent.yaml` 和可选的提示词文件，或直接使用 WebUI 的 **Agent 管理** 页面：

```yaml
# agent.yaml
name: ip-enrichment-worker
description: 专注于 IP 情报富化与威胁查询的专项子 Agent
role: worker
enabled: true
exec_skill_allowlist:
  - get-ip-info
  - virustotal-lookup
worker_max_steps: 3
```

主 Agent 将自动发现并在适当时委托给该子 Agent。

</details>

<details>
<summary><strong>主 Agent 如何决定是否使用子 Agent？</strong></summary>

主 Agent（Supervisor）通过 LangGraph 的 `ToolNode` 将所有可用子 Agent SubGraph 绑定为工具。在每个推理步骤，LLM 自主决策：顺序或并行调用子 Agent 工具、调用预设 Workflow，或直接结束编排返回结论。`worker_max_steps` 参数限制委托总次数，防止编排失控。

</details>

<details>
<summary><strong>自动执行模式是什么？</strong></summary>

开启后（通过配置中心、告警工作台，或对默认源使用 `SENTINELFLOW_AUTO_EXECUTE_ENABLED=true`），SentinelFlow 启动一个 asyncio 后台循环，持续拾取已开启自动执行的告警源中 `queued` 状态的任务，并通过 Agent 流水线自动处置，无需人工干预。如果某个源配置了失败重试间隔，该源下满足条件的失败任务也会在延迟后自动再次处置。可随时在 UI 中按源停止自动执行。

</details>

<details>
<summary><strong>没有 LLM API Key 也能运行 SentinelFlow 吗？</strong></summary>

WebUI 和告警接入流水线不依赖 LLM Key 即可正常运行。但 AI Agent 功能（多 Agent 编排、Skill 调用、LLM 辅助研判、解析规则生成）需要配置 LLM 端点。当 Agent 未配置时，`TriageService` 会提供基于规则的兜底处置结论。

</details>

<details>
<summary><strong>项目数据存储在哪里？</strong></summary>

- **Agent 定义**：默认位于 `.sentinelflow/plugins/agents/`
- **Skill 插件**：默认位于 `.sentinelflow/plugins/skills/`
- **Workflow 定义**：默认位于 `.sentinelflow/plugins/workflows/`
- **运行时配置**（WebUI 持久化）：默认位于 `.sentinelflow/runtime.json`
- **任务队列 / 审批记录**：`.sentinelflow/sys_queue.db`（SQLite）
- **运行日志**：默认位于 `.sentinelflow/run_logs/`
- **环境变量默认值**：项目根目录 `.env`（可选）

当前项目布局下，实际生效的本地工作区就是项目根 `.sentinelflow/` 目录。

</details>

<details>
<summary><strong>如何定义固定多步骤 Agent Workflow？</strong></summary>

在 `.sentinelflow/plugins/workflows/<workflow-id>/`（默认本地工作区）下创建 `workflow.json`，或使用 WebUI 的 **Workflow 管理** 页面。主 Agent 通过结构化 LLM 推理为来袭告警选择最优工作流，若无匹配则回退到自由 ReAct。v1.1.0 起，`run_workflow` 只负责加载固定计划；主 Agent 仍需亲自调用每个 Worker 步骤，并为每一步提供具体可执行的 task_prompt。

```json
{
  "id": "phishing-triage-v1",
  "name": "钓鱼告警研判工作流",
  "description": "标准钓鱼告警研判流程，含 URL 分析和发件人核查",
  "enabled": true,
  "scenarios": ["phishing", "suspicious_email"],
  "selection_keywords": ["钓鱼", "恶意链接", "可疑发件人"],
  "steps": [
    { "agent": "url-analysis-worker", "name": "URL 分析", "task_prompt": "分析告警中的 URL，识别恶意指标。" },
    { "agent": "sender-reputation-worker", "name": "发件人核查", "task_prompt": "核查发件人信誉和域名年龄。" },
    { "agent": "closure-worker", "name": "结单", "task_prompt": "根据以上研判结果，完成告警结单处置。" }
  ]
}
```

</details>

## 发布说明

- **v1.3.0** — 大队列性能加固、WebUI 实时刷新稳定性提升、更严格的真实动作完成语义，以及面向生产 SOC 场景的配置编辑体验优化。
  - **性能** — 告警状态接口改为轻量列表/标题查询、结果摘要字段反规范化、可索引 `sort_time` 排序、专用总览聚合和本周统计时间段聚合。
  - **WebUI** — 告警、总览、Skills、Agents 页面在高频轮询下通过共享缓存、后台刷新、强制刷新排队和更少重复请求保持流畅；设置页脏状态判断减少误报未保存改动。
  - **Runtime** — 纯文本 Skill 调用会规范化 JSON 字符串入参，向模型暴露授权 `input_schema` 提示，并保留更完整的校验/审计记录；委派的处置/结单任务现在必须产生真实结单 Skill 工具结果，不再接受仅文字声称完成。
  - **运营** — 总览与告警工作台在更大的 SQLite 队列下保持稳定，研判/处置展示的数据提取也更可靠。
- **v1.2.1** — WebUI 性能与布局优化、运行时审批/日志修复，以及完全独立的前端工程与样式体系。
  - **WebUI** — 告警、Skills、Agents 列表引入 stale-while-revalidate 缓存；告警工作台 / Skills / Agents 首屏加载更快；平台设置支持折叠区块与收起摘要；`Surface` 面板可折叠；对话消息支持折叠；态势总览任务生命周期中文化；任务中心执行详情展示增强；Markdown 样式与前端脚手架改为 SentinelFlow 自研实现。
  - **Runtime** — 修复二次审批复用旧 approval ID 的问题；工具调用摘要按真实执行时间线排序；提升 Skill 入参填写稳定性；思考模型顶层请求参数适配；优化运行日志展示提取逻辑。
  - **工程** — 移除遗留的第三方 UI 归因文件；运行时与业务页面保持 SentinelFlow 独立实现。
- **v1.2.0** — RAG 设置、运行日志追踪、面向更长任务的新上下文管理、`input_schema` 执行前校验、结单/闭环逻辑收紧、SQLite 与 poller 稳定性修复、告警工作台与 poll store 优化、思考模型适配，以及大量 WebUI 体验打磨。
- **v1.1.0** — 多 Agent 执行完整性、按源告警任务、Workflow Runner、prompt window 管理、运行日志追踪、RAG 设置与思考模型适配。

## 文档

当前 README 是主要使用说明。更完整的 Agent 配置、Skill 开发、Workflow 编写、API 参考和部署文档仍在规划中。

## 贡献

欢迎提交 Issue 反馈问题和建议！

提交 PR 前请确保：

- Python：`python -m pytest runtime/tests/` 全部通过
- Runtime 导入保持 `sentinelflow.*` 包路径形式
- 用户创建的 Skill、Agent 与 Workflow 应放在本地 `.sentinelflow/plugins/` 工作区，而不是写进包源码模块

新功能开发前，建议先开 Issue 讨论方案，不适合项目定位的功能性 PR 可能被关闭。

## 许可证

MIT License © SentinelFlow 贡献者

## 联系方式

- 📧 Email: ch1nfo@foxmail.com

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给一个 Star！⭐**

</div>
