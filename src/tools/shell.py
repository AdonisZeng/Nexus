"""Shell command execution tool"""
import asyncio
import logging
import shlex
from typing import Any
from pydantic import BaseModel, Field, field_validator
from .registry import Tool


logger = logging.getLogger(__name__)


class ShellArgs(BaseModel):
    """Shell command arguments"""
    command: str = Field(..., description="Shell command to execute")
    cwd: str | None = Field(None, description="Working directory for the command")
    timeout: int = Field(30, description="Command execution timeout in seconds", ge=1, le=300)
    max_output: int = Field(10000, description="Maximum output length in characters", ge=100, le=100000)
    background: bool = Field(False, description="Run command in background without blocking")

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Command cannot be empty")
        return v.strip()


class ShellTool(Tool):
    """Execute shell commands with safety checks"""

    #: Whether this tool mutates state
    is_mutating = True

    #: Safe commands that can be executed
    SAFE_COMMANDS = {
        "ls", "cat", "echo", "pwd", "which", "head", "tail",
        "find", "grep", "git", "ps", "df", "du", "wc", "whoami",
        "uname", "date", "file", "sort", "uniq", "awk", "sed",
        "dirname", "basename", "realpath", "readlink", "stat",
        "locate", "whereis", "history", "env", "printenv",
        "id", "groups", "who", "w", "uptime", "free", "top",
        "htop", "vmstat", "iostat", "netstat", "ss", "ping",
        "traceroute", "nslookup", "dig", "host", "curl", "wget",
        "chmod", "chown", "mkdir", "rmdir", "touch", "clear",
        "man", "info", "help", "type", "alias", "export",
        "python", "python3", "pip", "pip3", "node", "npm",
        "rustc", "cargo", "go", "java", "javac", "ruby", "gem",
        "docker", "docker-compose", "kubectl", "helm",
        # Windows commands
        "copy", "move", "del", "mkdir", "rmdir", "type", "cd", "dir",
        "findstr", "xcopy", "robocopy",
    }

    #: Git subcommands that are safe
    SAFE_GIT_COMMANDS = {
        "status", "log", "diff", "show", "branch", "remote",
        "config", "help", "version", "tag", "stash", "reflog",
    }

    #: Dangerous characters that could lead to command injection
    DANGEROUS_CHARS = {
        ">",  # Output redirection
        "|",  # Pipe
        ";",  # Command separator
        "&",  # Background or AND operator
        "$",  # Variable expansion
        "`",  # Command substitution
        "(",  # Subshell
        ")",  # Subshell
        "{",  # Brace expansion
        "}",  # Brace expansion
        "<",  # Input redirection
        "!",  # History expansion
        "*",  # Globbing (can be dangerous in some contexts)
        "?",  # Globbing
        "[",  # Globbing
        "]",  # Globbing
        "~",  # Tilde expansion
        "\\", # Escape character
    }

    #: Dangerous commands that should trigger warnings
    DANGEROUS_COMMANDS = {
        "rm", "mv", "cp", "dd", "mkfs", "fdisk", "parted",
        "mount", "umount", "reboot", "shutdown", "halt",
        "poweroff", "init", "systemctl", "service", "kill",
        "pkill", "killall", "chmod", "chown", "chgrp",
        "useradd", "userdel", "usermod", "groupadd", "groupdel",
        "passwd", "su", "sudo", "doas", "ssh", "scp", "sftp",
        "rsync", "curl", "wget", "nc", "netcat", "telnet",
        "ftp", "smbclient", "rpcclient", "ldapsearch",
        "openssl", "gpg", "pgp", "tar", "zip", "unzip",
        "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz",
        "7z", "rar", "unrar", "docker", "kubectl", "helm",
        "aws", "gcloud", "az", "terraform", "ansible",
        "puppet", "chef", "salt", "vagrant", "packer",
    }

    @property
    def name(self) -> str:
        return "shell_run"

    @property
    def description(self) -> str:
        return (
            "Run a shell command with safety checks. "
            "Input: command (string, required) - the command to execute, "
            "cwd (string, optional) - working directory, "
            "timeout (int, optional) - timeout in seconds (default: 30), "
            "max_output (int, optional) - max output length (default: 10000), "
            "background (bool, optional) - run in background without blocking (default: false)"
        )

    def is_safe_command(self, command: str) -> tuple[bool, str]:
        """
        Check if a command is safe to execute.

        Args:
            command: The command string to check

        Returns:
            Tuple of (is_safe, reason)
        """
        if not command or not command.strip():
            return False, "Command is empty"

        command = command.strip()

        # Check for dangerous characters
        for char in self.DANGEROUS_CHARS:
            if char not in command:
                continue

            # Special check for && and ||
            if char == "&" and "&&" not in command:
                continue  # Single & is for background, less dangerous
            if char == "|" and "||" not in command:
                return False, f"Command contains pipe operator '|' which can be dangerous"
            if char == "&" and "&&" in command:
                # Allow && if both commands are safe
                try:
                    parts = shlex.split(command.replace("&&", " "))
                    if parts and parts[0].lower() in self.SAFE_COMMANDS:
                        continue  # && with safe base command is OK
                except Exception:
                    pass
                return False, f"Command contains logical AND operator '&&' which can chain commands"
            if char == ";":
                return False, f"Command contains command separator ';' which can chain commands"
            if char == ">":
                return False, f"Command contains output redirection '>' which can overwrite files"
            if char == "<":
                return False, f"Command contains input redirection '<' which can read arbitrary files"
            if char in ("$", "`", "(", "{"):
                return False, f"Command contains shell expansion characters which can be dangerous"

            # Allow backslash in Windows paths (e.g., D:\path\to\file)
            if char == "\\":
                import re
                if re.search(r'[A-Za-z]:\\', command):
                    continue  # Windows path, allow backslash
                return False, f"Command contains dangerous character '\\'"

            # Allow asterisk in path components (e.g., member-*-dev, *.js)
            if char == "*":
                import re
                # Check if ALL asterisks appear to be part of paths
                all_in_path = True
                for match in re.finditer(r'\*', command):
                    pos = match.start()
                    before = command[pos - 1] if pos > 0 else ' '
                    after = command[pos + 1] if pos < len(command) - 1 else ' '
                    # If surrounded by path-like characters, it's likely in a path
                    if not (before.isalnum() or before in ('-', '_', '.', '\\', '/')):
                        all_in_path = False
                        break
                    if not (after.isalnum() or after in ('-', '_', '.', '\\', '/', ' ', '*')):
                        all_in_path = False
                        break
                if all_in_path:
                    continue  # All * are in path context, allow
                return False, f"Command contains dangerous character '*'"

            return False, f"Command contains dangerous character '{char}'"

        # Parse the command to get the base command
        try:
            # Use shlex to properly parse the command
            parts = shlex.split(command)
        except ValueError as e:
            return False, f"Failed to parse command: {e}"

        if not parts:
            return False, "Command is empty after parsing"

        base_cmd = parts[0].lower()

        # Handle git commands specially
        if base_cmd == "git":
            if len(parts) < 2:
                return True, "Git command with no subcommand"
            subcommand = parts[1].lower()
            if subcommand in self.SAFE_GIT_COMMANDS:
                return True, f"Safe git subcommand: {subcommand}"
            else:
                return False, f"Potentially unsafe git subcommand: {subcommand}"

        # Check if base command is in safe list
        if base_cmd in self.SAFE_COMMANDS:
            return True, f"Command '{base_cmd}' is in safe list"

        # Check if base command is dangerous
        if base_cmd in self.DANGEROUS_COMMANDS:
            return False, f"Command '{base_cmd}' is in dangerous command list"

        # Unknown command - be conservative
        return False, f"Command '{base_cmd}' is not in the safe command list"

    async def execute(self, command: str, cwd: str = None, timeout: int = 30, max_output: int = 10000, background: bool = False, **kwargs) -> str:
        """
        Execute shell command with safety checks.

        Args:
            command: Shell command to execute
            cwd: Working directory for the command
            timeout: Command execution timeout in seconds (default: 30)
            max_output: Maximum output length in characters (default: 10000)
            background: If True, run command in background without blocking

        Returns:
            Command output or error message
        """
        # Validate arguments using Pydantic model
        try:
            args = ShellArgs(command=command, cwd=cwd, timeout=timeout, max_output=max_output, background=background)
        except Exception as e:
            return f"Argument validation error: {e}"

        # Handle background execution
        if args.background:
            from .background import get_background_manager
            bg_manager = get_background_manager()
            return await bg_manager.run(args.command, args.cwd)

        # Safety check
        is_safe, reason = self.is_safe_command(args.command)

        if not is_safe:
            logger.warning(f"Blocked dangerous command: {args.command!r} - Reason: {reason}")
            return f"Error: Command blocked for safety reasons. {reason}"

        # Log warning for commands that might be risky
        if args.command.split()[0].lower() in self.DANGEROUS_COMMANDS:
            logger.warning(f"Executing potentially dangerous command: {args.command!r}")

        logger.info(f"Executing shell command: {args.command!r}")

        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    args.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=args.cwd
                ),
                timeout=args.timeout
            )

            stdout, stderr = await result.communicate()

            output = stdout.decode("utf-8", errors="ignore")
            error = stderr.decode("utf-8", errors="ignore")

            # Combine output and error
            full_output = ""
            if output:
                full_output += output
            if error:
                if full_output:
                    full_output += "\n\nSTDERR:\n"
                full_output += error

            # Apply output limit
            if len(full_output) > args.max_output:
                truncated = full_output[:args.max_output]
                full_output = truncated + f"\n\n[Output truncated: exceeded {args.max_output} character limit]"

            if result.returncode != 0:
                return f"Command failed with exit code {result.returncode}\n\n{full_output}"

            return full_output if full_output else "(command completed successfully)"

        except asyncio.TimeoutError:
            logger.warning(f"Command timed out after {args.timeout} seconds: {args.command!r}")
            return f"Error: Command timed out after {args.timeout} seconds"
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return f"Error executing command: {str(e)}"

    def _get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Command execution timeout in seconds (default: 30)",
                    "minimum": 1,
                    "maximum": 300
                },
                "max_output": {
                    "type": "integer",
                    "description": "Maximum output length in characters (default: 10000)",
                    "minimum": 100,
                    "maximum": 100000
                },
                "background": {
                    "type": "boolean",
                    "description": "Run command in background without blocking (default: false)"
                }
            },
            "required": ["command"]
        }
