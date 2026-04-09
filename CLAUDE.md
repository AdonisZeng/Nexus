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
- Built-in tools: FileReadTool, FileWriteTool, FileSearchTool, FilePatchTool, ListDirTool, ShellTool, CodeExecTool, SubagentTool, BackgroundRunTool, TodoTool

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
