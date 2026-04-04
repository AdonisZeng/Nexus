# Nexus - Personal AI Agent

## 项目概述

Nexus 是一个 Personal AI Agent CLI 应用，支持多模型、多 Agent 协作。

## Python 环境

激活虚拟环境后再安装 Python 包：

```powershell
D:\Development\Python\Nexus\.venv\Scripts\activate.ps1
uv pip install <package_name>
```

## 运行方式

```bash
python main.py                    # CLI 交互模式
python main.py "task"             # 单次任务
python main.py --model ollama     # 指定模型
python main.py --config custom.yaml
```

## 项目结构

```
src/
├── agent/      # Agent 核心逻辑
├── cli/        # CLI 入口界面
├── skills/     # 技能系统
├── context/    # 上下文管理
├── tools/      # 工具集
├── team/       # 多 Agent 协作
├── adapters/   # 模型适配器
├── mcp/        # MCP 服务器
├── tasks/      # 任务管理
├── todo/       # 待办事项
├── commands/   # 命令处理
├── config.py   # 配置加载
└── utils/      # 工具函数
```

## 配置 (config.yaml)

### 支持的模型
- `anthropic` - Claude 系列
- `openai` - GPT 系列
- `ollama` - 本地模型
- `lmstudio` - 本地模型
- `minimax` - MiniMax API
- `xai` - Grok 系列
- `custom` - 自定义 API

### Agent 参数
- `context_threshold` - 上下文阈值
- `max_iterations` - 最大迭代次数
- `reasoning` - 推理模式
- `verification` - 结果验证
- `planning` - 规划模式

## Agent Team 功能

### 何时使用
- 复杂任务可分解为独立功能模块
- 需要多专业角色并行工作
- 任务块之间相互独立可并行执行

### 何时不用
- 任务有严格依赖顺序 → 用 Tasks 模式
- 简单一次性任务 → 用普通对话
- 任务粒度太细 → 合并为粗粒度

### 触发关键词
- "使用Agent Team完成..."
- "使用Agent Team开发..."
- "使用Agent Team实现..."

### 工具使用
```
# 1. 创建团队
team(action="create", team_name="项目名", work_root="D:/workdir")

# 2. 生成 SPEC 规范
team(action="generate_spec", team_name="项目名", spec_content="...")

# 3. 添加任务到任务板
team(action="add_task", team_name="项目名", subject="任务1", description="...")
team(action="add_task", team_name="项目名", subject="任务2", description="...", blocked_by=[1])

# 4. 派生成员（系统自动命名为 member1, member2...）
team(action="spawn_autonomous", team_name="项目名", role="developer")
team(action="spawn_autonomous", team_name="项目名", role="developer")

# 5. 查看状态
team(action="status", team_name="项目名")
team(action="list_tasks", team_name="项目名")

# 6. 等待完成
team(action="await", team_name="项目名", timeout=300)

# 7. 关闭团队
team(action="shutdown", team_name="项目名")
```

### 成员规则
- 成员名称自动生成：member1, member2, member3...
- 所有成员角色统一为 developer（通用）
- 成员从任务板抢任务：先到先得
- 每个成员有独立的 Git Worktree 分支
- 成员只能在自己的 worktree 内写入文件
- 任务完成后通过 complete_task 合并到 work_root

### Lead Agent 约束
Lead Agent **不写代码**，只负责：
- 任务分解和分配
- 通过消息协调成员
- 监控进度和结果

禁止：
- 使用 file_write/shell_run 在成员 worktree 写代码
- 在 work_root 根目录写代码
- 直接操作成员文件系统

### 任务分配原则

**【关键】任务应该按文件边界分配，避免多成员编辑同一文件**

好例子：
- Task #1: 创建 `index.html` - 前端页面结构
- Task #2: 创建 `style.css` - 样式文件
- Task #3: 创建 `game.js` - 游戏逻辑

坏例子：
- Task #1: 实现前端界面和交互
- Task #2: 实现 AI 对战逻辑
（两者都可能编辑同一个文件，容易冲突）

**如果必须多成员编辑同一文件：**
- 使用 `blocked_by` 设置依赖关系
- 前置任务必须先完成并 merge 到 master
- 后置任务认领时会自动 merge master 获取前置成果

示例：
```
team(action="add_task", team_name="myteam",
     subject="创建前端界面", description="创建 index.html 和 style.css...")

team(action="add_task", team_name="myteam",
     subject="添加游戏逻辑", description="在 index.html 中添加 JavaScript...",
     blocked_by=[1])
```

**注意**：当前面的任务完成后，认领会自动将 master 分支 merge 到成员的 worktree，确保基于最新代码继续工作。

## 日志

日志位于 `logs/` 目录，格式：`YYYY-MM-DD_HH-MM-SS.txt`
