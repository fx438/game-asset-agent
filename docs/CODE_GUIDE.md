# 代码导读（CODE_GUIDE）

> 目标：读完这一份，能说清楚"一份需求文档上传后，代码是怎么一步步把它变成资源库里的图片的"。
> 所有 `文件:行号` 都可以在 IDE 里点击跳转。

## 目录

- [1. 一句话理解项目](#1-一句话理解项目)
- [2. 推荐阅读顺序](#2-推荐阅读顺序)
- [3. 目录与文件地图](#3-目录与文件地图)
  - [3.1 顶层文件](#31-顶层文件)
  - [3.2 app/ 基础设施层](#32-app-基础设施层)
  - [3.3 app/api/ 接口层](#33-appapi-接口层)
  - [3.4 app/workflow/ 流程编排层](#34-appworkflow-流程编排层)
  - [3.5 app/agents/ 四个子 Agent](#35-appagents-四个子-agent)
  - [3.6 app/ai/ 模型客户端](#36-appai-模型客户端)
  - [3.7 app/parsers/ 文档解析](#37-appparsers-文档解析)
  - [3.8 app/tasks/ 异步任务](#38-apptasks-异步任务)
  - [3.9 app/static/ 与 tests/](#39-appstatic-与-tests)
- [4. 分层与依赖关系](#4-分层与依赖关系)
- [5. 一个项目的完整生命周期（主线走读）](#5-一个项目的完整生命周期主线走读)
  - [5.1 上传文档](#51-上传文档)
  - [5.2 需求拆解](#52-需求拆解)
  - [5.3 确认清单 + 选风格](#53-确认清单--选风格)
  - [5.4 生成 / 确认风格母版](#54-生成--确认风格母版)
  - [5.5 批量生图（单张闭环）](#55-批量生图单张闭环)
  - [5.6 生成屏障：最后一张图唤醒流程](#56-生成屏障最后一张图唤醒流程)
  - [5.7 人工审核与驳回回流](#57-人工审核与驳回回流)
  - [5.8 归档入库](#58-归档入库)
- [6. 关键机制专题](#6-关键机制专题)
  - [6.1 interrupt / resume：流程怎么"暂停等人"](#61-interrupt--resume流程怎么暂停等人)
  - [6.2 令牌（token）防过期唤醒](#62-令牌token防过期唤醒)
  - [6.3 状态机与 CAS](#63-状态机与-cas)
  - [6.4 三种任务模式 celery / thread / sync](#64-三种任务模式-celery--thread--sync)
  - [6.5 提示词是怎么拼出来的](#65-提示词是怎么拼出来的)
  - [6.6 错误分类与重试](#66-错误分类与重试)
  - [6.7 mock 模式](#67-mock-模式)
- [7. 数据模型速查](#7-数据模型速查)
- [8. 状态速查表](#8-状态速查表)
- [9. 名词表](#9-名词表)
- [10. 动手验证建议](#10-动手验证建议)

---

## 1. 一句话理解项目

**FastAPI 接收用户操作 → 投递给 LangGraph 工作流 → 工作流调用 4 个子 Agent（需求拆解 / 提示词扩写 / 生图 / 审查）→ 结果写进数据库和对象存储。**

人工确认环节流程会"挂起"，等用户在前端点按钮后再"唤醒"继续。

---

## 2. 推荐阅读顺序

按这个顺序读，每一步只依赖前面读过的内容：

| 步骤 | 文件 | 读什么 | 预计时间 |
|---|---|---|---|
| 1 | [README.md](../README.md) | 功能、状态流转图 | 10 分钟 |
| 2 | [app/models.py](../app/models.py) | 5 张表 + 3 个状态枚举，**整个项目的"名词"** | 10 分钟 |
| 3 | [app/state_machine.py](../app/state_machine.py) | 项目状态允许怎么变 | 5 分钟 |
| 4 | [app/workflow/graph.py](../app/workflow/graph.py) | **核心**：9 个节点串起整条流程 | 30 分钟 |
| 5 | [app/agents/image_agent.py](../app/agents/image_agent.py) | 单张图"生成→审查→修正"闭环、生成屏障 | 15 分钟 |
| 6 | [app/agents/prompt_agent.py](../app/agents/prompt_agent.py) + [styles.py](../app/agents/styles.py) | 提示词怎么拼 | 10 分钟 |
| 7 | [app/agents/requirement_agent.py](../app/agents/requirement_agent.py) / [review_agent.py](../app/agents/review_agent.py) | 拆需求、审图 | 15 分钟 |
| 8 | [app/api/projects.py](../app/api/projects.py) / [review.py](../app/api/review.py) | 接口如何触发流程 | 10 分钟 |
| 9 | [app/tasks/dispatch.py](../app/tasks/dispatch.py) / [jobs.py](../app/tasks/jobs.py) | 任务怎么被异步执行 | 10 分钟 |
| 10 | [tests/test_workflow_e2e.py](../tests/test_workflow_e2e.py) | 用测试验证你的理解 | 10 分钟 |

剩下的 `config.py / db.py / storage.py / ai/ / parsers/` 都是"工具箱"，遇到再查即可。

---

## 3. 目录与文件地图

```
game-asset-agent/
├── README.md                  项目说明、运行方式
├── docs/CODE_GUIDE.md         本文件
├── requirements.txt           Python 依赖
├── Dockerfile                 API / worker 镜像
├── docker-compose.yml         PostgreSQL + Redis + MinIO + API + Celery
├── .env.example               环境变量模板（复制成 .env 使用）
├── app/
│   ├── main.py                FastAPI 入口：挂路由、静态页、启动建表
│   ├── config.py              所有配置（读环境变量）
│   ├── db.py                  数据库引擎、Session 管理
│   ├── models.py              ORM 表定义 + 状态枚举
│   ├── schemas.py             接口入参 / 出参（Pydantic）
│   ├── state_machine.py       项目状态机 + CAS
│   ├── storage.py             对象存储（MinIO / 本地磁盘）
│   ├── api/                   HTTP 接口
│   ├── workflow/graph.py      LangGraph 主流程（核心）
│   ├── agents/                4 个子 Agent + 风格模板
│   ├── ai/                    百炼文本/视觉模型、万相生图客户端
│   ├── parsers/               docx/xlsx/pdf/csv/txt 解析
│   ├── tasks/                 Celery / 线程池 / 同步 三种执行方式
│   └── static/index.html      Vue 3 单页前端（无限画布）
└── tests/                     单元测试 + 端到端测试
```

### 3.1 顶层文件

| 文件 | 作用 |
|---|---|
| `requirements.txt` | 依赖：FastAPI、SQLAlchemy、LangGraph、Celery、openai(兼容百炼)、httpx、Pillow、python-docx、openpyxl、pdfplumber、minio 等 |
| `docker-compose.yml` | 基础设施；`--profile app` 时额外启动 API 和 worker |
| `.env.example` | 配置模板；底部有"本地开发配置"（SQLite + 线程池 + 本地磁盘） |

### 3.2 app/ 基础设施层

| 文件 | 关键内容 | 说明 |
|---|---|---|
| [main.py](../app/main.py) | `lifespan` :18、`index` :38 | 启动时 `init_db()` 建表；注册 `projects / review / misc` 三组路由，统一前缀 `/api` |
| [config.py](../app/config.py) | `Settings` :8、`get_settings` :87 | 全部配置项；`mock_ai` 属性：没填 API Key 就自动 mock，`demo_mode` 时无条件 mock；`is_postgres` 决定 checkpoint 用哪种 |
| [db.py](../app/db.py) | `session_scope` :31、`get_db` :44 | **业务代码统一用 `with session_scope() as s:`**，正常结束自动 commit，异常 rollback |
| [models.py](../app/models.py) | 5 张表 | 见 [第 7 节](#7-数据模型速查) |
| [schemas.py](../app/schemas.py) | `PlanConfirm`、`MasterConfirm`、`HumanReview`… | 接口请求体校验和返回结构 |
| [state_machine.py](../app/state_machine.py) | `TRANSITIONS` :11、`transition` :36、`compare_and_set` :43 | 见 [6.3](#63-状态机与-cas) |
| [storage.py](../app/storage.py) | `LocalStorage` :22、`MinioStorage` :49、`get_storage` :88 | 两种实现同一接口：`put_bytes / get_bytes / copy / exists`；本地实现有路径穿越校验 |

### 3.3 app/api/ 接口层

接口层**只做三件事**：校验参数和状态 → 改少量数据库字段 → 调 `dispatch.enqueue_workflow(...)` 把"唤醒值"交给工作流。真正的业务推进都不在这里。

| 文件 | 接口 | 行号 |
|---|---|---|
| [projects.py](../app/api/projects.py) | `POST /projects` 上传并启动 | :37 |
| | `GET /projects/{id}` 详情 + 进度统计 | :63 |
| | `POST /projects/{id}/plan` 确认清单 + 选风格 | :75 |
| | `GET /projects/{id}/masters` 母版历史 | :90 |
| | `POST /projects/{id}/master` 确认 / 驳回母版 | :98 |
| [review.py](../app/api/review.py) | `GET /projects/{id}/assets` 资源列表 | :29 |
| | `POST /assets/{id}/review` 单张审核 | :60 |
| | `POST /projects/{id}/review/batch` 批量审核 | :71 |
| | `POST /projects/{id}/review/submit` 提交本轮审核 | :82 |
| | `GET /projects/{id}/library` 已入库资源 | :95 |
| [misc.py](../app/api/misc.py) | `/styles` 风格列表、`/system` 运行模式、`/files/{key}` 读文件（只允许 `projects/`、`library/` 前缀） | :14 / :19 / :26 |

注意 `review.py` 的两个集合：
- `DECIDABLE` :14 —— 人工可以操作的图片状态；
- `UNDECIDED` :15 —— 还没做决定的状态，存在这些图时不允许提交。

### 3.4 app/workflow/ 流程编排层

[graph.py](../app/workflow/graph.py) 是全项目的**主干**，一定要精读。

| 节点 / 函数 | 行号 | 做什么 | 是否等人 |
|---|---|---|---|
| `parse_requirements` | :50 | 解析文档 → 需求拆解 Agent → 写 `asset_tasks` | |
| `_await` | :69 | 封装 `interrupt()`，校验唤醒类型和令牌 | |
| `confirm_plan` | :83 | 接收用户修改后的清单和风格；生成角色设定卡 | ✅ |
| `generate_master` | :116 | 生成一版风格母版（带重试） | |
| `confirm_master` | :134 | 确认 → 去批量生图；驳回 → 带意见回 `generate_master` | ✅ |
| `dispatch_generation` | :150 | 拆帧、拼提示词、建 `assets` 记录、并发投递生图任务 | |
| `wait_generation` | :178 | 等待"生成屏障"唤醒 | ✅（等系统） |
| `human_review` | :185 | 有驳回且未超轮次 → 回流；否则归档 | ✅ |
| `regenerate_rejected` | :202 | 把人工批注写进提示词，只重投被驳回的图 | |
| `archive` | :239 | 按规范路径复制图片 + 写 `manifest.json` | |
| `build_graph` | :270 | 注册节点和固定边 | |
| `_checkpointer` | :286 | PostgreSQL 用 `PostgresSaver`，否则 `InMemorySaver` | |
| `project_lock` | :313 | 同一项目串行推进（Redis 锁 / 进程内锁） | |
| `run_workflow` | :336 | **对外唯一入口**：首次启动或 `Command(resume=...)` 唤醒 | |

流程图（摘自文件头注释）：

```
parse_requirements -> confirm_plan(人工) -> generate_master -> confirm_master(人工)
      ┌──────────── 不满意，带意见重生成 ────────────┘
      ↓ 满意，锁定风格
dispatch_generation -> wait_generation(等待批量生图屏障) -> human_review(人工)
      ↑                                                   │ 有驳回且未超轮数
regenerate_rejected <──────────────────────────────────────┘
                                                          │ 全部处理完
                                                       archive -> END
```

固定边写在 `build_graph` 里；人工节点之后往哪走，由节点返回的 `Command(goto=...)` 决定。

### 3.5 app/agents/ 四个子 Agent

| 文件 | 对外函数 | 职责 |
|---|---|---|
| [requirement_agent.py](../app/agents/requirement_agent.py) | `run` :143 | 先 `extract_from_tables` :74 按列名别名读表；读不出再走 LLM（或 mock 正则）；最后 `normalize` :102 去重、截断、记录 issues |
| [prompt_agent.py](../app/agents/prompt_agent.py) | `build_character_cards` :35、`expand_frames` :46、`compose_prompt` :62、`build_master_prompt` :67、`refine` :80 | LLM 只产出"内容"（设定卡、帧描述、修正项），最终提示词由 `compose_prompt` 固定模板拼装 |
| [styles.py](../app/agents/styles.py) | `STYLES`、`get_style` | 5 种风格的前缀 + 负向提示词 |
| [image_agent.py](../app/agents/image_agent.py) | `generate_master` :35、`process_asset` :58、`mark_error` :115、`notify_if_batch_done` :121 | 生母版；单张图闭环；生成屏障 |
| [review_agent.py](../app/agents/review_agent.py) | `review` :54、`rule_check` :29 | 规则检查 → qwen-vl 打分 → **代码按阈值判定是否通过** |

### 3.6 app/ai/ 模型客户端

| 文件 | 说明 |
|---|---|
| [errors.py](../app/ai/errors.py) | `AIError`（不可重试）与子类 `TransientAIError`（可重试）。**整个项目的重试逻辑都靠这两个类区分** |
| [dashscope_llm.py](../app/ai/dashscope_llm.py) | 用 OpenAI SDK 的兼容模式调百炼；`chat_json`（文本）/ `vision_json`（看图，图片转 base64）；JSON 解析失败会带错误信息重问一次 |
| [wanx_client.py](../app/ai/wanx_client.py) | 万相异步接口：`submit` 提交 → `poll` 轮询 → `download` 下载；`generate` 是统一入口；mock 时 `_mock_generate` 优先用 `example_images` 的示例图，否则由 `pixel_sprites` 画像素小人 |

### 3.7 app/parsers/ 文档解析

[document_parser.py](../app/parsers/document_parser.py)：把各种格式统一成 `ParsedDocument(text, tables)`。表格单独保留，是为了让需求拆解 Agent 能直接按表读取，不必交给大模型猜。

### 3.8 app/tasks/ 异步任务

| 文件 | 说明 |
|---|---|
| [dispatch.py](../app/tasks/dispatch.py) | **业务代码只调这里**：`enqueue_workflow` :71、`enqueue_asset` :80。根据 `task_mode` 决定走 Celery、线程池还是同步队列 |
| [jobs.py](../app/tasks/jobs.py) | Celery 任务定义；`generate_asset_impl` :13 是三种模式共用的生图逻辑，**无论成功失败最后都会检查生成屏障** |
| [celery_app.py](../app/tasks/celery_app.py) | Celery 配置：`acks_late`、`prefetch=1`、两个队列 `images` / `workflow` |

### 3.9 app/static/ 与 tests/

- [static/index.html](../app/static/index.html)：Vue 3 单页无限画布，直接调用上面的 `/api/*`，不需要构建。五个流程节点和按"英雄 · 动作"分组的图片画板放在同一个可平移缩放的世界层里，节点位置存在 localStorage。
- [tests/conftest.py](../tests/conftest.py)：测试固定使用 SQLite + `sync` 模式 + mock。
- [tests/test_units.py](../tests/test_units.py)：状态机、表格抽取、解析、规则检查等单元测试。
- [tests/test_workflow_e2e.py](../tests/test_workflow_e2e.py)：`test_full_workflow` 走完整条流程，**是最好的"可执行说明书"**。

---

## 4. 分层与依赖关系

```
            ┌────────────────────────────┐
  浏览器 ──▶│ api/        (校验 + 投递)   │
            └─────────────┬──────────────┘
                          │ dispatch.enqueue_workflow(project_id, resume)
            ┌─────────────▼──────────────┐
            │ tasks/      (怎么执行)      │  celery / thread / sync
            └─────────────┬──────────────┘
                          │ run_workflow()
            ┌─────────────▼──────────────┐
            │ workflow/graph.py (流程)    │──▶ state_machine.py（状态校验）
            └─────────────┬──────────────┘
                          │ 调用
            ┌─────────────▼──────────────┐
            │ agents/     (业务能力)      │
            └──────┬──────────────┬──────┘
                   │              │
         ┌─────────▼───┐   ┌──────▼─────────┐
         │ ai/ 模型     │   │ parsers/ 解析   │
         └─────────────┘   └────────────────┘

  横向基础设施：config.py  db.py  models.py  storage.py
```

规律：**上层调下层，下层不知道上层**。唯一的"反向"调用是 `image_agent.notify_if_batch_done` 和 `graph.dispatch_generation` 会调用 `tasks.dispatch` 去投递任务（因此在函数内部 `import`，避免循环导入）。

---

## 5. 一个项目的完整生命周期（主线走读）

下面跟着一个项目从上传到入库走一遍。每一步写明：**谁触发 → 调了什么 → 状态变成什么**。

### 5.1 上传文档

1. 前端 `POST /api/projects` → [projects.py:37](../app/api/projects.py#L37) `create_project`
2. 校验后缀和大小，建 `Project` 行，原文件存到 `projects/{id}/source/requirement.xxx`
3. `dispatch.enqueue_workflow(project.id)` —— **不带 resume，表示首次启动**
4. 最终进入 [graph.py:336](../app/workflow/graph.py#L336) `run_workflow`，`graph.invoke({"project_id": id})`

状态：`CREATED`

### 5.2 需求拆解

节点 [parse_requirements](../app/workflow/graph.py#L50)：

1. 状态 → `PARSING`
2. `parse_document()` 把文件转成 `ParsedDocument`
3. `requirement_agent.run(doc)`：
   - 表格能识别出"英雄 / 动作 / 数量"列 → 直接读（`method=table`）
   - 否则 → qwen-plus JSON 输出（或 mock 正则）
   - `normalize` 合并重复、截断超量，问题写入 `issues`
4. 写 `asset_tasks`、`parse_issues`，状态 → `PLAN_CONFIRMING`
5. 进入 `confirm_plan` 节点，执行到 `interrupt()` **挂起**，`invoke` 返回

### 5.3 确认清单 + 选风格

1. 用户在前端修改清单、选风格 → `POST /plan` → [projects.py:75](../app/api/projects.py#L75)
2. 接口投递 `resume={"type": "confirm_plan", "style_key": ..., "tasks": ...}`
3. 工作流从 [confirm_plan](../app/workflow/graph.py#L83) 的 `interrupt()` 处继续，拿到这个字典
4. 用户改了清单就整体替换 `tasks`；调 `build_character_cards` 为每个"英雄|角色"生成一张外观设定卡
5. 状态 → `MASTER_GENERATING`，跳到 `generate_master`

### 5.4 生成 / 确认风格母版

- [generate_master](../app/workflow/graph.py#L116) → [image_agent.generate_master](../app/agents/image_agent.py#L35)：用第一个任务的角色 + 风格前缀拼母版提示词，**随机 seed**，生成并存为 `projects/{id}/masters/v{n}.png`。状态 → `MASTER_CONFIRMING`
- [confirm_master](../app/workflow/graph.py#L134) 挂起等用户：
  - 确认 → 母版标记 `APPROVED`，去 `dispatch_generation`
  - 驳回（必须填意见）→ 母版标记 `REJECTED`，状态回 `MASTER_GENERATING`，带 `master_feedback` 再生一版

### 5.5 批量生图（单张闭环）

[dispatch_generation](../app/workflow/graph.py#L150)：

1. 对每个任务 `expand_frames` 拆成 N 帧描述
2. 每帧 `compose_prompt` 拼出完整提示词，建一条 `Asset`（状态 `PENDING`）
3. **先**把项目状态提交为 `GENERATING`，**再**逐张 `enqueue_asset` —— 顺序很重要，见 5.6
4. 节点结束，走固定边到 `wait_generation` 挂起

每张图在 worker 里执行 [image_agent.process_asset](../app/agents/image_agent.py#L58)：

```
状态 GENERATING
  └─ loop:
       version += 1；seed = 母版seed + attempts（首次与母版相同）
       wanx_client.generate(...)  → 存 projects/{pid}/assets/{aid}/v{version}.png
       状态 AUTO_REVIEWING
       review_agent.review(新图, 母版图, 任务描述) → 写一条 ReviewRecord(kind=auto)
       ├─ 通过            → WAITING_HUMAN，结束
       ├─ 次数用尽        → AUTO_FAILED，结束（交给人裁决）
       └─ 不通过          → prompt_agent.refine 产出修正项，重拼提示词，继续 loop
```

注意：数据库事务**只包住读写**，调用模型的网络请求放在事务外，避免长事务。

### 5.6 生成屏障：最后一张图唤醒流程

每张图结束（无论成功、失败、异常）都会走到 [jobs.py:13](../app/tasks/jobs.py#L13) 末尾的 `notify_if_batch_done` → [image_agent.py:121](../app/agents/image_agent.py#L121)：

1. 统计本项目还处于 `PENDING / GENERATING / AUTO_REVIEWING` 的图片数
2. 不为 0 → 什么都不做
3. 为 0 → `compare_and_set(GENERATING → HUMAN_REVIEWING)`
4. 只有 CAS 成功的那个 worker 投递 `resume={"type": "wait_generation", "token": 当前轮次}`
5. 工作流 `wait_generation` 被唤醒 → 进入 `human_review` 挂起

为什么 5.5 要"先提交 GENERATING 再投递"？因为如果项目还没是 `GENERATING`，最后一张图做 CAS 会失败，流程就永远等不到唤醒。

### 5.7 人工审核与驳回回流

1. 用户逐张或批量审核：[review.py:47](../app/api/review.py#L47) `_apply_review` 把图片改成 `APPROVED` / `REJECTED`（驳回必须写批注），写 `ReviewRecord(kind=human)`
2. 用户点"提交本轮"：[review.py:82](../app/api/review.py#L82)，检查没有 `UNDECIDED` 的图，投递 `resume={"type": "human_review", "token": review_round}`
3. [human_review](../app/workflow/graph.py#L185) 再兜底检查一次，然后：
   - 有驳回且 `review_round < max_human_rounds` → [regenerate_rejected](../app/workflow/graph.py#L202)
   - 否则 → `archive`（被驳回但超轮次的图不会入库）
4. `regenerate_rejected`：批注经 `refine` 变成修正项，**累积**写进 `Asset.human_fix`；`review_round += 1`；只把被驳回的图重置为 `PENDING` 重新投递；回到 `wait_generation`

### 5.8 归档入库

[archive](../app/workflow/graph.py#L239)：

1. 状态 → `ARCHIVING`
2. 所有 `APPROVED` 的图复制到
   `library/{项目ID}_{项目名}/{英雄}/{角色}/{动作}/{英雄}_{角色}_{动作}_{序号}.png`
3. 写 `manifest.json`（prompt、负向、seed、模型、风格、母版版本）
4. 图片状态 → `ARCHIVED`，项目状态 → `ARCHIVED`，流程 `END`

---

## 6. 关键机制专题

### 6.1 interrupt / resume：流程怎么"暂停等人"

- 节点里调用 `interrupt(x)`：LangGraph 把当前进度存进 checkpointer，`graph.invoke` 直接返回。
- 之后调用 `graph.invoke(Command(resume=v), config)`：从**同一个节点开头重新执行**，这次 `interrupt()` 直接返回 `v`。
- `config` 中的 `thread_id = "project-{id}"`（[graph.py:332](../app/workflow/graph.py#L332)）把每个项目区分开。
- 因为节点会重跑，`interrupt()` 之前的代码要可重复执行——本项目的人工节点都把 `_await` 放在第一行。

### 6.2 令牌（token）防过期唤醒

[_await](../app/workflow/graph.py#L69) 检查唤醒值：`type` 必须是当前节点期待的，`token` 必须匹配，否则返回 `None`，节点 `goto` 自己重新等待。

| 环节 | token |
|---|---|
| `confirm_master` | 最新母版版本号 |
| `wait_generation` / `human_review` | `Project.review_round` |

场景：用户连点两次"提交审核"。第一次触发了重生成、`review_round` 变成 1；第二次的 token 还是 0，被丢弃，不会把新一轮还没审的图直接归档。

### 6.3 状态机与 CAS

- [TRANSITIONS](../app/state_machine.py#L11)：每个状态允许去哪些状态。所有 `transition()` 都先校验，非法就抛 `InvalidTransition`。
- [compare_and_set](../app/state_machine.py#L43)：`UPDATE projects SET status=B WHERE id=? AND status=A`，看 `rowcount==1` 判断是不是自己改成功的。多个 worker 同时完成时只有一个赢。
- 接口层的 `require_status`（[projects.py:32](../app/api/projects.py#L32)）是第一道关；工作流内的 `transition` 是第二道关。

### 6.4 三种任务模式 celery / thread / sync

由 `TASK_MODE` 环境变量决定，入口都在 [dispatch.py](../app/tasks/dispatch.py)：

| 模式 | 用途 | 执行方式 | 流程锁 | checkpoint |
|---|---|---|---|---|
| `celery` | 生产 / Docker | 投递到 Redis，worker 执行 | Redis 分布式锁 | PostgresSaver（可重启续跑） |
| `thread` | 无 Redis 本地开发 | 进程内线程池 | 进程内锁 | InMemorySaver（重启丢失） |
| `sync` | 测试 | 放进队列，由最外层调用者按顺序跑完 | 进程内锁 | InMemorySaver |

`sync` 模式用 `_drain_sync` 防止"在工作流节点里投递工作流"造成递归。

### 6.5 提示词是怎么拼出来的

[compose_prompt](../app/agents/prompt_agent.py#L62) 固定顺序：

```
风格前缀（styles.py） ， 用户风格补充 ， 角色：英雄（角色），设定卡 ， 动作：帧描述 ， 视角：xx ， 修正项
```

风格一致靠三件事：同一风格前缀 + 负向提示词；沿用母版 seed；同一角色共用一张设定卡。
修正项 = `human_fix`（人工批注，累积保留）+ 本次自动审查的 `fix`。万相上限 500 字，超长从末尾截断（[wanx_client.py](../app/ai/wanx_client.py) `_clip`）。

### 6.6 错误分类与重试

| 层级 | 机制 |
|---|---|
| 模型调用 | `TransientAIError`（限流、超时、5xx）可重试；`AIError`（参数错、内容审核拦截）不重试 |
| JSON 解析 | `dashscope_llm._chat_json` 失败后带错误信息重问一次 |
| 单张生图 | Celery：`self.retry` 指数退避 10/20/40s；thread/sync：`_run_asset_with_retry` 最多 4 次 |
| 最终失败 | `mark_error` 把图置为 `ERROR`，**仍会检查屏障**，人工阶段可驳回重生成 |
| 母版生成 | `tenacity` 重试 3 次 |
| 流程异常 | `run_workflow` 捕获后把项目置 `FAILED` 并记录 `error` |

### 6.7 mock 模式

不填 `DASHSCOPE_API_KEY` 时 `settings.mock_ai == True`，各处分支：

- 生图：示例需求用 `app/static/examples/frames/` 里的示例图；其他需求由 `pixel_sprites.py` 画像素小人（英雄定配色、职业定武器、动作和帧序号定姿势）
- 审查：按图片 md5 打分，约 30% 不通过（用于覆盖重试分支）
- 需求拆解：正则识别"英雄 角色 动作 N张"
- 设定卡 / 帧描述 / 修正项：固定模板文本

`DEMO_MODE=true`（在线演示）时会清空 Key、强制 mock，并收紧单任务 / 单项目张数和上传大小。

阅读时可以搜索 `mock_ai` 快速找到所有 mock 分支。

---

## 7. 数据模型速查

```
Project 1 ── N AssetTask 1 ── N Asset 1 ── N ReviewRecord
   │
   └── 1 ── N StyleMaster
```

| 表 | 一行代表 | 重要字段 |
|---|---|---|
| `projects` | 一个出图项目 | `status`、`style_key`、`character_cards`（JSON：`"英雄\|角色" → 设定卡`）、`review_round`、`error` |
| `asset_tasks` | 清单的一行：某英雄/角色/动作/N 张 | `hero`、`role`、`action`、`count`、`view`、`description` |
| `style_masters` | 一版风格母版 | `version`（项目内唯一递增）、`seed`、`status`、`feedback` |
| `assets` | 一张图（重生成只更新同一行） | `seq`、`prompt`、`seed`、`version`（每生成一次+1）、`attempts`（本轮自动重试次数）、`human_fix`、`archive_key` |
| `review_records` | 一次审查记录 | `kind`（auto/human）、`passed`、`content_score`、`style_score`、`issues` |

---

## 8. 状态速查表

**项目状态 `ProjectStatus`**

| 状态 | 含义 | 下一步由谁推动 |
|---|---|---|
| CREATED | 刚上传 | 系统 |
| PARSING | 需求拆解中 | 系统 |
| PLAN_CONFIRMING | 等用户确认清单 | **用户** `POST /plan` |
| MASTER_GENERATING | 生成母版中 | 系统 |
| MASTER_CONFIRMING | 等用户确认母版 | **用户** `POST /master` |
| GENERATING | 批量生图 + 自动审查 | 系统（生成屏障） |
| HUMAN_REVIEWING | 等人工审核 | **用户** `POST /review/submit` |
| ARCHIVING → ARCHIVED | 归档中 → 完成 | 系统 |
| FAILED | 异常终止 | — |

**图片状态 `AssetStatus`**

```
PENDING → GENERATING ⇄ AUTO_REVIEWING ─┬→ WAITING_HUMAN ─┐
                                       └→ AUTO_FAILED  ──┼→ APPROVED → ARCHIVED
                  (接口彻底失败) ERROR ───────────────────┘→ REJECTED → PENDING（回流）
```

`ACTIVE_STATUSES = PENDING / GENERATING / AUTO_REVIEWING`（[image_agent.py:22](../app/agents/image_agent.py#L22)），生成屏障就是数这三种。

---

## 9. 名词表

| 名词 | 解释 |
|---|---|
| 风格母版 | 一张定画风的样图，用户确认后锁定 seed 和风格 |
| 角色设定卡 | 每个"英雄\|角色"一段外观描述，所有动作复用 |
| 帧描述 | 一个动作拆成 N 张时，每张的姿态描述 |
| 修正项 / fix | 根据审查问题追加到提示词末尾的描述 |
| human_fix | 人工批注转成的修正项，跨轮累积，不会被自动修正覆盖 |
| 生成屏障 | "最后一张图完成时唤醒流程"的机制 |
| CAS | Compare-And-Set，带条件的原子更新 |
| checkpointer | LangGraph 保存流程进度的组件 |
| resume / 唤醒值 | 接口投递给工作流、让挂起节点继续的字典 |
| token / 令牌 | 唤醒值里的版本号或轮次，用来丢弃过期唤醒 |

---

## 10. 动手验证建议

读完后用下面的方法确认理解：

1. **跑测试**：`pytest -q`，然后在 [test_workflow_e2e.py](../tests/test_workflow_e2e.py) 的 `test_full_workflow` 里逐段对照第 5 节。
2. **打断点**：在 `graph.py` 每个节点函数第一行打断点，用 `sync` 模式跑测试，观察执行顺序和 `interrupt` 后的返回。
3. **本地起服务（mock）**：`.env` 中设置 `DATABASE_URL=sqlite:///./.data/app.db`、`TASK_MODE=thread`、`STORAGE_BACKEND=local`，执行 `uvicorn app.main:app --reload`，打开 http://localhost:8000 点一遍完整流程，同时看日志。
4. **自问自答**：
   - 用户连点两次"确认母版"会发生什么？（提示：6.2）
   - 两张图同时生成完，为什么只会唤醒一次流程？（提示：5.6、6.3）
   - 人工驳回两轮后，第二轮的提示词里还有第一轮的批注吗？（提示：`human_fix`）
   - 重启进程后，`thread` 模式和 `celery` 模式的进行中项目有什么区别？（提示：6.4）
