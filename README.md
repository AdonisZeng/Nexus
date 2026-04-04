# Nexus - Personal AI Agent

[中文文档](doc/README_CN.md)

A personal AI Agent CLI application built with Python, supporting multiple models and multi-agent collaboration.

## Features

### Multi-Model Support

Unified interface for various LLM providers:

| Model | Description |
|-------|-------------|
| Anthropic | Claude series |
| OpenAI | GPT series |
| Ollama | Local models |
| LM Studio | Local models |
| xAI | Grok series |
| MiniMax | MiniMax API |
| Custom | Custom API (Anthropic/OpenAI compatible) |

### Tool Capabilities

- File read/write and search
- Shell command execution
- Code execution
- Office document processing (Word, Excel, PPT, PDF)
- Browser automation (Selenium, Playwright)
- MCP protocol extension

### Working Modes

- **Normal Chat** - Daily Q&A and simple tasks
- **Plan Mode** - Complex task decomposition and sequential execution
- **Tasks Mode** - Project-level task management with dependency graph
- **Agent Team** - Multi-agent parallel collaboration

---

## EXE Usage

### Directory Structure

First run of `Nexus.exe` automatically creates:

```
Nexus/
├── Nexus.exe          # Executable
├── config.yaml        # Configuration file
├── logs/              # Log directory
└── _internal/         # Runtime dependencies
```

### Launch Methods

```powershell
# Double-click to run - Interactive mode

# Command line
.\Nexus.exe                           # Interactive mode
.\Nexus.exe "Analyze current project"  # Single task
.\Nexus.exe --model ollama            # Specify model
.\Nexus.exe --config D:\custom.yaml   # Custom config
```

### Command Line Arguments

| Argument | Description |
|----------|-------------|
| `task` | Execute a single task |
| `--model` | Specify model (anthropic/openai/ollama/lmstudio/custom) |
| `--config` | Specify configuration file path |

---

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/exit` | Exit program |
| `/clear` | Clear conversation |
| `/models` | List available models |
| `/settings` | View settings |
| `/sessions` | Manage sessions |
| `/restore` | Restore session |
| `/reload` | Reload configuration |
| `/plan` | Enter Plan mode |
| `/tasks` | Enter Tasks mode |
| `/teams` | Manage Agent Team |
| `/mcpstatus` | MCP server status |

---

## Plan Mode

Suitable for complex tasks that need to be decomposed into sequential steps.

### How to Enable

```
/plan
```

### Workflow

1. Enter `/plan` to activate plan mode
2. Describe your task goal
3. Agent analyzes and generates task list
4. Confirm to execute tasks sequentially
5. Auto-exit after completion

### Example

```
> /plan
Plan mode enabled, please describe your task

> Create a Python Web project with user authentication and REST API

[Agent generates task list]
1. Analyze requirements and plan project structure
2. Create project base files
3. Implement user authentication module
4. Implement REST API endpoints
5. Add test files

Execute this plan? (y/n) y

[Executing task 1/5]
...
```

### Use Cases

- Tasks requiring multiple steps
- Tasks with sequential dependencies
- Tasks needing planning before execution

---

## Tasks Mode

Suitable for complex projects, supports task dependency graph and persistent storage.

### How to Enable

```
/tasks
```

### Workflow

1. Enter `/tasks` to activate Tasks mode
2. Describe project requirements
3. Agent analyzes and generates task graph with dependencies
4. Confirm to execute tasks by dependency order
5. Supports resume from breakpoint

### Comparison with Plan Mode

| Feature | Plan Mode | Tasks Mode |
|---------|-----------|------------|
| Task Storage | Memory | Persistent |
| Dependencies | Sequential | DAG dependency graph |
| Parallel Execution | No | Yes |
| Resume | No | Yes |
| Use Case | Single complex task | Project-level development |

### Example

```
> /tasks
Tasks mode enabled, please describe your task

> Develop a Tetris game with scoring system and next piece preview

[Agent analyzes and generates dependency graph]
Project: tetris

Tasks:
#1 Analyze requirements and plan structure [no dependency]
#2 Create index.html page structure [depends on #1]
#3 Create style.css stylesheet [depends on #1]
#4 Create game.js game logic [depends on #2, #3]
#5 Test and fix issues [depends on #4]

Execute? (y/n) y

[Executing task #1]
[Executing tasks #2, #3 in parallel]
[Executing task #4]
[Executing task #5]
```

---

## Agent Team

Multi-agent parallel collaboration for large-scale project development.

### Use Cases

**Suitable for:**
- Tasks decomposable into independent modules
- Parallel development of multiple files
- Clear file boundaries between tasks

**Not suitable for:**
- Tasks with strict sequential dependencies → Use Tasks mode
- Simple tasks → Use normal chat

### How to Enable

Use the `team` tool in conversation:

```
Create an Agent Team to develop this project
```

### Workflow

```
# 1. Create team
team(action="create", team_name="myproject", work_root="D:/workspace")

# 2. Generate SPEC (optional)
team(action="generate_spec", team_name="myproject", spec_content="...")

# 3. Add tasks to task board
team(action="add_task", team_name="myproject", subject="Create frontend page", description="...")
team(action="add_task", team_name="myproject", subject="Create backend API", description="...")
team(action="add_task", team_name="myproject", subject="Add tests", description="...", blocked_by=[1, 2])

# 4. Spawn members
team(action="spawn_autonomous", team_name="myproject", role="developer")
team(action="spawn_autonomous", team_name="myproject", role="developer")

# 5. Check status
team(action="status", team_name="myproject")

# 6. Wait for completion
team(action="await", team_name="myproject", timeout=300)

# 7. Shutdown team
team(action="shutdown", team_name="myproject")
```

### team Tool Parameters

| action | Description | Parameters |
|--------|-------------|------------|
| `create` | Create team | team_name, work_root |
| `generate_spec` | Generate spec | team_name, spec_content |
| `add_task` | Add task | team_name, subject, description, blocked_by(optional) |
| `spawn_autonomous` | Spawn member | team_name, role |
| `status` | View status | team_name |
| `list_tasks` | List tasks | team_name |
| `await` | Wait for completion | team_name, timeout |
| `shutdown` | Shutdown team | team_name |

### Task Assignment Principles

**Assign tasks by file boundaries to avoid multiple members editing the same file**

Good example:
```
#1 Create index.html
#2 Create style.css
#3 Create app.js
```

Bad example:
```
#1 Implement frontend UI
#2 Implement interaction logic
(May edit same file, causing conflicts)
```

### Setting Task Dependencies

Use `blocked_by` parameter to set dependencies:

```
team(action="add_task", team_name="myproject", 
     subject="Add tests", 
     description="...",
     blocked_by=[1, 2])  # Wait for tasks 1 and 2 to complete
```

### Managing Teams

Use `/teams` command to manage created teams:

```
/teams
```

Displays team list with options to:
- View team members
- Delete team
- View member status

---

## Configuration

Configuration file `config.yaml` is located in the same directory as the exe.

### Setting API Key

**Method 1: Environment Variable (Recommended)**

```yaml
models:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

**Method 2: Direct Input (Not Recommended)**

```yaml
models:
  anthropic:
    api_key: sk-ant-xxx
```

### Switching Default Model

```yaml
models:
  default: ollama  # Switch to ollama
```

### Local Model Configuration

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

### Agent Parameters

```yaml
agent:
  context_threshold: 80000   # Context threshold
  max_iterations: 10         # Max iterations
  reasoning: true            # Reasoning mode
  verification: true         # Result verification
```

---

## Logs

Log files are located in `logs/` directory, format: `YYYY-MM-DD_HH-MM-SS.txt`

---

## License

[MIT License](LICENSE)
