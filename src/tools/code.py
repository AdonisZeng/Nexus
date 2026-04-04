"""Code execution tool"""
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from .registry import Tool


class CodeExecArgs(BaseModel):
    """Arguments for code execution tool"""
    code: str = Field(..., description="Python code to execute", min_length=1)


class CodeExecTool(Tool):
    """Execute code in a sandbox"""

    is_mutating = True  # type: ignore

    @property
    def name(self) -> str:
        return "code_exec"

    @property
    def description(self) -> str:
        return "Execute Python code and return the result. Input: code (string, required) - Python code to execute"

    def _truncate_output(self, output: str, limit: int = 10000) -> str:
        """Truncate output if it exceeds the limit"""
        if len(output) > limit:
            return output[:limit] + "\n... (output truncated)"
        return output

    async def execute(self, code: str, **kwargs) -> str:
        """Execute Python code"""
        # Validate arguments using Pydantic model
        try:
            args = CodeExecArgs(code=code)
        except Exception as e:
            return f"Error: Invalid arguments - {str(e)}"

        temp_file = None
        process = None

        try:
            # Create a temporary file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(args.code)
                temp_file = f.name

            # Execute with timeout using asyncio.wait_for
            process = await asyncio.create_subprocess_exec(
                "python",
                temp_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except asyncio.TimeoutError:
                # Kill the process if it timed out
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                return "Error: Code execution timed out (30s limit)"

            output = stdout.decode("utf-8", errors="ignore")
            error = stderr.decode("utf-8", errors="ignore")

            # Apply output limits
            output = self._truncate_output(output)
            error = self._truncate_output(error)

            if process.returncode != 0:
                return f"Error (exit code {process.returncode}):\n{error}" if error else f"Error: Process exited with code {process.returncode}"

            if error:
                return f"Output:\n{output}\n\nWarnings/Errors:\n{error}" if output else f"Warnings/Errors:\n{error}"

            return output if output else "(no output)"

        except FileNotFoundError:
            return "Error: Python interpreter not found. Please ensure Python is installed and available in PATH."
        except PermissionError:
            return "Error: Permission denied when trying to execute the code."
        except OSError as e:
            return f"Error: System error occurred - {str(e)}"
        except Exception as e:
            return f"Error: Unexpected error - {str(e)}"
        finally:
            # Clean up temporary file
            if temp_file:
                try:
                    Path(temp_file).unlink(missing_ok=True)
                except Exception:
                    pass

    def _get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                }
            },
            "required": ["code"]
        }