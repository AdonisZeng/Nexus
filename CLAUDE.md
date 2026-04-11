# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nexus is a personal AI Agent CLI application supporting multiple LLM providers (Anthropic, OpenAI, Ollama, LM Studio, xAI, MiniMax, Custom) and multi-agent collaboration. Built with Python, packaged as both CLI and standalone exe via PyInstaller.

## Setup

```bash
# Install dependencies
uv pip install -r requirements.txt

# Config is auto-created from config.yaml.template on first run
# API keys via environment variables: ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, MINIMAX_API_KEY, CUSTOM_API_KEY
```

## Running the Application

```bash
# CLI mode (interactive)
python main.py

# Single task
python main.py "Analyze current directory structure"

# Specify model
python main.py --model ollama

# Custom config
python main.py --config custom.yaml
```

## Building the Exe

```bash
python build.py
# Output: dist/Nexus/Nexus.exe
```

## Architecture

### Module Dependency Graph

```
main.py
    ├── bootstrap.py          # PyInstaller runtime init
    ├── config.py             # Config loading
    └── cli/main.py (NexusCLI) # Main orchestrator
            ├── agent/session.py (AgentSession)  # Task execution
            │       ├── adapters/                # LLM providers (self-registering)
            │       ├── tools/orchestrator.py    # Tool lifecycle
            │       │       └── tools/registry.py # Tool registry
            │       ├── mcp/client.py            # MCP protocol client
            │       └── context/                 # Memory & compression
            ├── commands/                       # Slash commands
            ├── skills/                         # Dynamic skill loader
            └── team/                           # Multi-agent collaboration
```

### Core Modules

| Module | Responsibility | Key Components |
|--------|---------------|-----------------|
| `src/cli/` | UI layer & session orchestration | `NexusCLI`, `rich_ui.py` |
| `src/agent/` | Task execution engine | `AgentSession`, `AgentLoop` |
| `src/adapters/` | LLM provider interface | `ModelAdapter`, `AdapterRegistry` (auto-register) |
| `src/tools/` | Tool system | `Tool`, `ToolRegistry`, `ToolOrchestrator`, `ToolGate` |
| `src/context/` | Memory & context compression | `AgentContext`, three-tier compression |
| `src/mcp/` | MCP protocol client | `MCPClient` (stdio/http) |
| `src/commands/` | Slash commands | `CommandRegistry`, built-in commands |
| `src/skills/` | Dynamic capabilities | `SkillCatalog` (two-layer: index + on-demand load) |
| `src/team/` | Multi-agent system | `TeamManager`, `MessageBus`, `TaskBoard` |
| `src/tasks/` | DAG task management | `TaskManager` (JSON persistence) |
| `src/permissions/` | Permission system | `PermissionChecker`, `ToolGate` |
| `src/hooks/` | Global hooks | `HookManager`, `HookRunner` |
| `src/error/` | Error handling | Centralized error types |

### Key Design Patterns

**1. Dependency Injection via `ModelProvider` Interface**
```python
class ModelProvider(ABC):
    def get_adapter() -> ModelAdapter
    def set_adapter(adapter: ModelAdapter)
```
Chained: `NexusCLI` → `AgentSession` → `TeamTool`

**2. Self-Registration Pattern**
Adapters use `__init_subclass__` to auto-register to `AdapterRegistry`. Commands similarly auto-register.

**3. Delegation Pattern**
`NexusCLI` handles only UI and session management; actual execution delegated to `AgentSession`.

**4. Two-Layer Skill System**
- Layer 1: `describe_available()` returns name:description catalog
- Layer 2: `load_full_text()` loads actual SKILL.md content (LRU cached)

**5. Context Compression Tiers**
```
Tier 1: ToolOutputPersister  - Persist large tool outputs to disk
Tier 2: MicroCompactor       - Compress old tool results
Tier 3: LLMContextCompressor - LLM summarization
```

### Initialization Flow

```
main.py → bootstrap() → load_config() → NexusCLI.initialize()
    ├── _create_model_adapter()      # From config
    ├── AgentSession(adapter)        # Creates tool registry, MCP client
    │       └── ToolOrchestrator(ToolGate())
    ├── _connect_mcp_servers()        # Background MCP connections
    └── SystemPromptBuilder.build()  # Compose system prompt
```

### Tool Execution Lifecycle (`ToolOrchestrator`)

```
hook:tool_call_start → permission_check → [gate.wait() if mutating]
    → tool.before_execute() → tool.execute() → tool.after_execute()
    → hook:tool_call_end → [gate.release() if mutating]
```

### Model Adapters (`src/adapters/`)
Unified interface for LLM providers. Base class `ModelAdapter` defines `chat()`, `chat_with_tools()`, `get_name()`, `supports_streaming()`. Each provider (anthropic, openai, ollama, lmstudio, xai, custom) implements this interface.

### Agent System (`src/agent/`)
- `AgentLoop` - Execution loop with iteration control, state tracking, context compression, retry logic
- `AgentContext` - Conversation state and message history management
- `WorkItem` - Task unit processed by the agent loop

### Tool System (`src/tools/`)
- `Tool` (ABC) - Base class for all tools with `execute()`, `before_execute()`, `after_execute()`
- `ToolRegistry` - Global registry of available tools
- `ToolOrchestrator` - Manages tool execution lifecycle with gate-based concurrency control
- Built-in tools: FileReadTool, FileWriteTool, FileSearchTool, FilePatchTool, ListDirTool, ShellTool, CodeExecTool, SubagentTool, CheckSubagentTool, CancelSubagentTool, BackgroundRunTool, CheckBackgroundTool, TodoTool

#### SubAgent Tool (`src/tools/subagent/`)
配置从 `~/.nexus/agents/*.md` 文件加载，支持的 frontmatter 字段：
- `name`, `description` - 代理名称和描述
- `system_prompt` - 系统提示（YAML frontmatter 后的内容）
- `allowed-tools` / `denied-tools` - 工具白名单/黑名单
- `required-tools` - 强制包含的工具（优先级最高）
- `result-mode` - 输出模式：`summary`（简洁）或 `detailed`（含 iterations/tokens）
- `initial-prompt` - 在 system_prompt 之前注入的初始化指令
- `parallel-tasks` - 预定义的并行任务列表
- `permission-mode` - 权限模式：`normal` 或 `read_only`
- `tool-parameters` - 工具参数限制
- `background` - 是否默认后台执行

SubAgent 工具：
- `subagent` - 调用子代理（支持 `parallel_tasks` 参数实现并行执行）
- `check_subagent` - 检查后台子代理任务状态
- `cancel_subagent` - 取消运行中的后台子代理任务

### Task System (`src/tasks/`)
- `TaskManager` - Persistent task storage with DAG dependency graph
- Tasks stored as individual JSON files in `.nexus/tasks/`
- Supports `blocked_by` dependencies, ready-task detection

### Team System (`src/team/`)
- `TeamManager` - Team lifecycle and member coordination
- `MessageBus` - Inter-agent message passing
- `TaskBoard` - Task assignment and tracking
- `WorkTreeManager` - Git worktree isolation per agent
- Max 10 members per team

### CLI (`src/cli/`)
- `NexusCLI` - Main CLI orchestrator
- `rich_ui.py` - Rich console UI utilities
- Plan mode and Tasks mode integration

### Skills System (`src/skills/`)
Dynamically loaded capabilities matched to user intent. Skills are registered at startup and selected by the `matcher` based on the prompt context.

### Bootstrap (`src/bootstrap.py`)
Handles exe runtime initialization: creates `logs/` directory, copies `config.yaml.template` → `config.yaml` if missing, ensures `~/.nexus/` exists.

## Working Modes

1. **Normal Chat** - Simple Q&A with tool access
2. **Plan Mode** (`/plan`) - Task decomposition with sequential execution
3. **Tasks Mode** (`/tasks`) - DAG-based project tasks with persistence and parallel execution
4. **Agent Team** - Multi-agent parallel collaboration via `team()` tool

### Available Slash Commands
`/plan`, `/tasks`, `/teams`, `/models`, `/sessions`, `/settings`, `/reload`, `/restore`, `/clear`, `/help`, `/exit`

## Key Patterns

### Tool Execution
Tools are coordinated by `ToolOrchestrator` which manages `ToolGate` for mutating operations. Mutating tools (file_write, shell, etc.) acquire exclusive access via gate.wait()/release().

### Context Management
- `AgentContext` holds short-term memory (recent messages) and long-term memory (persisted session)
- Context compression triggers at `context_threshold` (default 80000 chars)
- Supports summarization for extreme length

### Model Provider Selection
Configured in `config.yaml` under `models` section. Provider selected via `models.default` or CLI `--model` flag. API keys support `${ENV_VAR}` syntax for environment variable substitution.

## File Organization

```
src/
├── adapters/       # LLM provider adapters
├── agent/          # Agent loop and context
├── cli/            # CLI interface
├── commands/       # Built-in slash commands
├── config.py       # Config loading with env var support
├── context/        # Context management
├── mcp/            # MCP protocol client
├── skills/         # Skill system
├── tasks/          # Task management
├── team/           # Multi-agent team system
├── tools/          # Tool implementations
├── todo/           # Todo functionality
└── utils/          # Utilities (logger, tokenizer, etc.)
```
