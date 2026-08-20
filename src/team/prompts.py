"""Agent Team usage guide for LLM"""

AGENT_TEAM_USAGE_GUIDE = """
## Agent Team 功能

当你需要完成复杂任务时，可以考虑使用 Agent Team 进行多 Agent 并行协作。

### 何时使用 Agent Team

- 任务可以分解为多个独立的、功能性的模块
- 需要多个专业角色并行工作（如前端+后端+测试）
- 任务块之间相互独立，可以并行执行

### 何时使用其他方式

- 任务有严格依赖顺序 → 使用 Tasks 模式
- 简单的一次性任务 → 使用普通对话或 Subagent
- 任务粒度太细 → 合并为粗粒度块

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

### 如何触发

当用户说以下内容时，使用 team 工具：
- "使用Agent Team完成..."
- "使用Agent Team开发..."
- "使用Agent Team实现..."

### team 工具使用

**1. 创建空团队（自主模式）**

使用 work_root 指定工作目录，成员将在 Git Worktree 中工作：
```
team(action="create", team_name="项目名", work_root="D:/work")
```

**2. 添加任务**

向任务板添加任务，成员会自主认领：
```
team(action="add_task", team_name="项目名", subject="任务1", description="...")
team(action="add_task", team_name="项目名", subject="任务2", blocked_by=[1])
```

**3. 派生自主成员**

派生后会从任务板认领任务并工作：
```
team(action="spawn_autonomous", team_name="项目名", name="coder1", role="developer")
```

**4. 团队管理**

- 使用 team(action="status", team_name="...") 查看进度
- 使用 team(action="list_tasks", team_name="...") 查看任务列表
- 使用 team(action="send", ...) 向特定成员发消息
- 使用 team(action="broadcast", ...) 向所有成员发消息
- 使用 team(action="await", ...) 等待完成

### 任务规划建议

- **2-3 个 Agent**：适合简单到中等复杂度任务
- **4-5 个 Agent**：适合中等复杂度任务
- **最多 10 个 Agent**：适合非常复杂的任务
- **每个 Agent 任务应独立**：减少成员间依赖
"""

AGENT_TEAM_TRIGGER_PATTERNS = [
    "使用Agent Team",
    "使用 Agent Team",
    "agent team",
    "Agent Team",
]
