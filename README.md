# Nexus - Personal AI Agent

使用 Python 构建的个人 AI Agent CLI 应用，支持多模型、多 Agent 协作。

## 功能列表

### 多模型支持

统一接口对接多种 LLM 服务商：

| 模型 | 说明 |
|------|------|
| Anthropic | Claude 系列 |
| OpenAI | GPT 系列 |
| Ollama | 本地模型 |
| LM Studio | 本地模型 |
| xAI | Grok 系列 |
| MiniMax | MiniMax API |
| Custom | 自定义 API（兼容 Anthropic/OpenAI 协议） |

### 工具能力

- 文件读写与搜索
- Shell 命令执行
- 代码执行
- Office 文档处理（Word、Excel、PPT、PDF）
- 浏览器自动化（Selenium、Playwright）
- MCP 协议扩展

### 工作模式

- **普通对话** - 日常问答和简单任务
- **Plan 模式** - 复杂任务分解与顺序执行
- **Tasks 模式** - 项目级任务管理，支持依赖图
- **Agent Team** - 多 Agent 并行协作

---

## EXE 使用方法

### 目录结构

首次运行 `Nexus.exe` 会自动创建：

```
Nexus/
├── Nexus.exe          # 可执行文件
├── config.yaml        # 配置文件
├── logs/              # 日志目录
└── _internal/         # 运行时依赖
```

### 启动方式

```powershell
# 双击运行 - 交互模式

# 命令行运行
.\Nexus.exe                           # 交互模式
.\Nexus.exe "分析当前项目结构"         # 单次任务
.\Nexus.exe --model ollama            # 指定模型
.\Nexus.exe --config D:\custom.yaml   # 指定配置
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `task` | 执行单次任务 |
| `--model` | 指定模型 (anthropic/openai/ollama/lmstudio/custom) |
| `--config` | 指定配置文件路径 |

---

## 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/exit` | 退出程序 |
| `/clear` | 清空对话 |
| `/models` | 列出可用模型 |
| `/settings` | 查看设置 |
| `/sessions` | 管理会话 |
| `/restore` | 恢复会话 |
| `/reload` | 重载配置 |
| `/plan` | 进入 Plan 模式 |
| `/tasks` | 进入 Tasks 模式 |
| `/teams` | 管理 Agent Team |
| `/mcpstatus` | MCP 服务器状态 |

---

## Plan 模式

适用于需要分解为多个步骤顺序执行的复杂任务。

### 启用方式

```
/plan
```

### 工作流程

1. 输入 `/plan` 进入计划模式
2. 描述你的任务目标
3. Agent 自动分析并生成任务列表
4. 确认后按顺序逐个执行
5. 完成后自动退出

### 示例

```
> /plan
计划模式已启用，请输入任务描述

> 帮我创建一个 Python Web 项目，包含用户认证和 REST API

[Agent 生成任务列表]
1. 分析需求并规划项目结构
2. 创建项目基础文件
3. 实现用户认证模块
4. 实现 REST API 接口
5. 添加测试文件

是否执行此计划？(y/n) y

[开始执行任务 1/5]
...
```

### 适用场景

- 需要多步骤完成的任务
- 任务之间有顺序依赖
- 需要先规划再执行

---

## Tasks 模式

适用于复杂项目，支持任务依赖图和持久化存储。

### 启用方式

```
/tasks
```

### 工作流程

1. 输入 `/tasks` 进入 Tasks 模式
2. 描述项目需求
3. Agent 分析生成任务图（包含依赖关系）
4. 确认后按依赖顺序执行
5. 支持断点续执行

### 与 Plan 模式的区别

| 特性 | Plan 模式 | Tasks 模式 |
|------|----------|-----------|
| 任务存储 | 内存 | 持久化 |
| 依赖关系 | 顺序执行 | DAG 依赖图 |
| 并行执行 | 不支持 | 支持 |
| 断点续执行 | 不支持 | 支持 |
| 适用场景 | 单次复杂任务 | 项目级开发 |

### 示例

```
> /tasks
Tasks 模式已启用，请输入任务描述

> 开发一个俄罗斯方块游戏，包含分数系统和下一个方块预览

[Agent 分析任务并生成依赖图]
项目名称: tetris

任务列表:
#1 分析需求并规划项目结构 [无依赖]
#2 创建 index.html 页面结构 [依赖 #1]
#3 创建 style.css 样式文件 [依赖 #1]
#4 创建 game.js 游戏逻辑 [依赖 #2, #3]
#5 测试并修复问题 [依赖 #4]

是否执行？(y/n) y

[执行任务 #1]
[执行任务 #2, #3 并行]
[执行任务 #4]
[执行任务 #5]
```

---

## Agent Team

多 Agent 并行协作，适用于大型项目开发。

### 适用场景

**适合使用：**
- 任务可分解为独立模块
- 需要并行开发多个文件
- 任务之间文件边界清晰

**不适合使用：**
- 任务有严格顺序依赖 → 用 Tasks 模式
- 简单任务 → 用普通对话

### 启用方式

在对话中使用 `team` 工具：

```
创建一个 Agent Team 来开发这个项目
```

### 使用流程

```
# 1. 创建团队
team(action="create", team_name="myproject", work_root="D:/workspace")

# 2. 生成 SPEC 规范（可选）
team(action="generate_spec", team_name="myproject", spec_content="项目规范...")

# 3. 添加任务到任务板
team(action="add_task", team_name="myproject", subject="创建前端页面", description="...")
team(action="add_task", team_name="myproject", subject="创建后端 API", description="...")
team(action="add_task", team_name="myproject", subject="添加测试", description="...", blocked_by=[1, 2])

# 4. 派生成员
team(action="spawn_autonomous", team_name="myproject", role="developer")
team(action="spawn_autonomous", team_name="myproject", role="developer")

# 5. 查看状态
team(action="status", team_name="myproject")

# 6. 等待完成
team(action="await", team_name="myproject", timeout=300)

# 7. 关闭团队
team(action="shutdown", team_name="myproject")
```

### team 工具参数

| action | 说明 | 参数 |
|--------|------|------|
| `create` | 创建团队 | team_name, work_root |
| `generate_spec` | 生成规范 | team_name, spec_content |
| `add_task` | 添加任务 | team_name, subject, description, blocked_by(可选) |
| `spawn_autonomous` | 派生成员 | team_name, role |
| `status` | 查看状态 | team_name |
| `list_tasks` | 列出任务 | team_name |
| `await` | 等待完成 | team_name, timeout |
| `shutdown` | 关闭团队 | team_name |

### 任务分配原则

**按文件边界分配任务，避免多个成员编辑同一文件**

好例子：
```
#1 创建 index.html
#2 创建 style.css
#3 创建 app.js
```

坏例子：
```
#1 实现前端界面
#2 实现交互逻辑
（可能编辑同一文件，产生冲突）
```

### 设置任务依赖

使用 `blocked_by` 参数设置依赖关系：

```
team(action="add_task", team_name="myproject", 
     subject="添加测试", 
     description="...",
     blocked_by=[1, 2])  # 等待任务 1 和 2 完成后才能开始
```

### 管理团队

使用 `/teams` 命令管理已创建的团队：

```
/teams
```

显示团队列表，支持：
- 查看团队成员
- 删除团队
- 查看成员状态

---

## 配置

配置文件 `config.yaml` 位于 exe 同目录。

### 设置 API Key

**方式1：环境变量（推荐）**

```yaml
models:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

**方式2：直接写入（不推荐）**

```yaml
models:
  anthropic:
    api_key: sk-ant-xxx
```

### 切换默认模型

```yaml
models:
  default: ollama  # 改为使用 ollama
```

### 本地模型配置

**Ollama:**

```yaml
models:
  ollama:
    url: http://localhost:11434
    model: llama3
```

**LM Studio:**

```yaml
models:
  lmstudio:
    url: http://localhost:1234/v1
    model: qwen/qwen3.5-35b-a3b
```

### Agent 参数

```yaml
agent:
  context_threshold: 80000   # 上下文阈值
  max_iterations: 10         # 最大迭代次数
  reasoning: true            # 推理模式
  verification: true         # 结果验证
```

---

## 日志

日志文件位于 `logs/` 目录，格式：`YYYY-MM-DD_HH-MM-SS.txt`

---

## 许可证

[MIT License](LICENSE)
