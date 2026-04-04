"""
Agent Loop - Iteration control and monitoring for Nexus Agent System

This module provides the AgentLoop class for controlling the agent execution loop:
- Maximum iteration limits
- State tracking
- Termination condition checking
- Tool call history
- Monitoring and logging
- Error retry mechanism
- Context length management
- Thinking mode support
- Result verification
- Execution planning
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Awaitable
from enum import Enum
import time
import logging
import asyncio
import re

from .context import AgentContext, ConversationState, ToolCallEntry, ContextMessage
from .work_item import WorkItemSource, WorkItem
from src.tools.tracker import ToolCallTracker

logger = logging.getLogger(__name__)


class LoopEvent(Enum):
    """Events that can occur during agent loop."""
    ITERATION_START = "iteration_start"
    ITERATION_END = "iteration_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TERMINATED = "terminated"
    TIMEOUT = "timeout"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    TASK_COMPLETED = "task_completed"
    ERROR = "error"
    CONTEXT_COMPRESSED = "context_compressed"
    CONTEXT_SUMMARIZED = "context_summarized"
    THINKING = "thinking"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    PLAN_GENERATED = "plan_generated"
    PLAN_ADJUSTED = "plan_adjusted"
    # New events for mode integration
    WORK_ITEM_START = "work_item_start"
    WORK_ITEM_END = "work_item_end"
    WORK_ITEM_CONFIRMATION_NEEDED = "work_item_confirmation_needed"
    USER_CONFIRMATION_NEEDED = "user_confirmation_needed"
    USER_CONFIRMATION_RESULT = "user_confirmation_result"


@dataclass
class LoopMetrics:
    """Metrics collected during agent loop execution."""
    total_iterations: int = 0
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    total_time_seconds: float = 0.0
    iteration_times: list[float] = field(default_factory=list)


class LoopCallbacks:
    """Callbacks for loop events."""

    def __init__(self):
        self._callbacks: dict[LoopEvent, list[Callable]] = {e: [] for e in LoopEvent}

    def on(self, event: LoopEvent, callback: Callable) -> None:
        """Register a callback for an event."""
        self._callbacks[event].append(callback)

    async def emit(self, event: LoopEvent, **kwargs: Any) -> None:
        """Emit an event to all registered callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(**kwargs)
                else:
                    callback(**kwargs)
            except Exception as e:
                logger.error(f"Error in loop callback for {event}: {e}")


class AgentLoop:
    """Controls the agent execution loop with iteration and state management."""

    def __init__(
        self,
        context: Optional[AgentContext] = None,
        max_iterations: int = 10,
        timeout_seconds: float = 300.0,
        max_retries: int = 3,
        context_threshold: int = 80000,
        reasoning: bool = False,
        verification: bool = False,
        planning: bool = False,
        on_iteration_start: Optional[Callable[[int], Awaitable[None]]] = None,
        on_iteration_end: Optional[Callable[[int, bool], Awaitable[None]]] = None,
        on_terminate: Optional[Callable[[str], Awaitable[None]]] = None,
        work_item_source: Optional[WorkItemSource] = None,
        on_work_item_confirmation: Optional[Callable[[str], Awaitable[Optional[bool]]]] = None,
        on_user_confirmation: Optional[Callable[[list[str]], Awaitable[Optional[bool]]]] = None,
        on_confirmation_check: Optional[Callable[[str, str], Awaitable[Optional[bool]]]] = None,
    ):
        """
        Initialize the agent loop.

        Args:
            context: AgentContext instance (will create if not provided)
            max_iterations: Maximum number of iterations allowed
            timeout_seconds: Timeout in seconds
            max_retries: Maximum number of retries for failed tool calls
            context_threshold: Character threshold for context compression
            reasoning: Enable thinking mode
            verification: Enable result verification
            planning: Enable execution planning
            on_iteration_start: Callback at start of each iteration
            on_iteration_end: Callback at end of each iteration
            on_terminate: Callback when loop terminates
            work_item_source: Optional WorkItemSource for mode-specific work items
            on_work_item_confirmation: Callback to confirm work item completion
            on_user_confirmation: Callback to request user confirmation (Plan mode)
            on_confirmation_check: Callback for SubagentRunner-style completion confirmation
                                    Args: (response, stop_reason) -> Optional[bool]
                                    Return True if confirmed complete, False if not confirmed, None to skip
        """
        self.context = context or AgentContext()
        self.context.state.max_iterations = max_iterations
        self.context.state.timeout_seconds = timeout_seconds

        self.max_retries = max_retries
        self.context_threshold = context_threshold
        self.reasoning = reasoning
        self.verification = verification
        self.planning = planning

        self.callbacks = LoopCallbacks()
        self.metrics = LoopMetrics()
        self.tool_tracker = ToolCallTracker()

        if on_iteration_start:
            self.callbacks.on(LoopEvent.ITERATION_START, on_iteration_start)
        if on_iteration_end:
            self.callbacks.on(LoopEvent.ITERATION_END, on_iteration_end)
        if on_terminate:
            self.callbacks.on(LoopEvent.TERMINATED, on_terminate)

        self._iteration_start_time: float = 0.0
        self._current_plan: list[str] = []
        self._retry_count: int = 0

        # Work item source for mode-specific work acquisition
        self.work_item_source = work_item_source
        self._on_work_item_confirmation = on_work_item_confirmation
        self._on_user_confirmation = on_user_confirmation
        self._current_work_item: Optional[WorkItem] = None

        # Deferred work item completion - set in execute_with_tools, consumed in run()
        self._pending_work_item: Optional[WorkItem] = None
        self._pending_work_response: str = ""

        # Work item retry tracking
        self._failed_work_item: Optional[WorkItem] = None
        self._failed_work_error: str = ""
        self._retry_count: int = 0
        self._max_retries_per_task: int = 3

        # Stop reason tracking for confirmation flow (SubagentRunner)
        self._last_stop_reason: Optional[str] = None
        self._on_confirmation_check = on_confirmation_check

    def record_tool_call(self, tool_name: str, args: dict, result: Any, success: bool):
        """Record a tool call and update metrics."""
        self.tool_tracker.record(tool_name, args, result, success)
        self.metrics.total_tool_calls += 1
        if success:
            self.metrics.successful_tool_calls += 1
        else:
            self.metrics.failed_tool_calls += 1

    def get_tool_summary(self) -> str:
        """Get a summary of all tool calls."""
        return self.tool_tracker.get_summary()

    @property
    def state(self) -> ConversationState:
        """Get the conversation state."""
        return self.context.state

    @property
    def iteration(self) -> int:
        """Get current iteration number."""
        return self.context.state.iteration

    @property
    def should_continue(self) -> bool:
        """Check if the loop should continue."""
        return not self.context.state.is_finished

    @property
    def current_work_item(self) -> Optional[WorkItem]:
        """Get the current work item being processed."""
        return self._current_work_item

    def can_continue(self) -> bool:
        """Check if we can continue to next iteration (read-only check)."""
        state = self.context.state

        if state.should_terminate:
            return False

        if state.is_timed_out:
            return False

        if state.iteration >= state.max_iterations:
            return False

        return True

    def _update_state_on_cannot_continue(self) -> str:
        """Update state when cannot continue. Returns termination reason."""
        state = self.context.state

        if state.should_terminate:
            return "Terminate requested"

        if state.is_timed_out:
            state.mark_timeout()
            return "Task timed out"

        if state.iteration >= state.max_iterations:
            reason = f"Max iterations reached ({state.max_iterations})"
            state.mark_finished(reason)
            return reason

        return "Unknown termination reason"

    async def execute_with_retry(
        self,
        func: Callable[[], Awaitable[Any]],
        max_retries: int = None,
    ) -> tuple[Any, bool]:
        """
        Execute a function with exponential backoff retry.

        Args:
            func: Async function to execute
            max_retries: Override max retries for this call

        Returns:
            (result, success) tuple
        """
        if max_retries is None:
            max_retries = self.max_retries

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = await func()
                if attempt > 0:
                    logger.info(f"Retry {attempt} succeeded after previous failure")
                return result, True
            except Exception as e:
                last_error = e
                self._retry_count += 1
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"All {max_retries + 1} attempts failed: {e}")

        return None, False

    def calculate_context_length(self) -> int:
        """Calculate total character length of context messages."""
        total = 0
        for msg in self.context.short_term_memory:
            total += len(msg.content)
        return total

    def compress_context(self, keep_recent: int = 10) -> int:
        """
        Compress context by removing older messages.

        Args:
            keep_recent: Number of recent messages to keep

        Returns:
            Number of messages removed
        """
        if len(self.context.short_term_memory) <= keep_recent:
            return 0

        removed = len(self.context.short_term_memory) - keep_recent
        self.context.short_term_memory = self.context.short_term_memory[-keep_recent:]
        logger.info(f"Compressed context: removed {removed} older messages")
        return removed

    async def summarize_and_compress(self, summarize_fn: Callable[[list], Awaitable[str]]) -> bool:
        """
        Summarize older messages and replace them with a summary.

        Args:
            summarize_fn: Async function that takes messages and returns a summary

        Returns:
            True if summarization succeeded
        """
        if not self.context.short_term_memory:
            return False

        keep_recent = 5
        to_summarize = self.context.short_term_memory[:-keep_recent]
        recent = self.context.short_term_memory[-keep_recent:]

        if not to_summarize:
            return False

        try:
            messages_for_summary = [msg.to_dict() for msg in to_summarize]
            summary = await summarize_fn(messages_for_summary)

            summary_msg = ContextMessage(
                role="system",
                content=f"[Previous conversation summary]\n{summary}",
                metadata={"type": "summary"}
            )

            self.context.short_term_memory = [summary_msg] + recent
            logger.info("Context summarized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to summarize context: {e}")
            return False

    async def check_and_compress_context(
        self,
        summarize_fn: Callable[[list], Awaitable[str]] = None
    ) -> bool:
        """
        Check if context needs compression and compress if needed.

        Args:
            summarize_fn: Optional async function for summarization

        Returns:
            True if compression was performed
        """
        current_length = self.calculate_context_length()

        if current_length > self.context_threshold:
            logger.info(
                f"Context length ({current_length}) exceeds threshold "
                f"({self.context_threshold}), compressing..."
            )

            await self.callbacks.emit(LoopEvent.CONTEXT_COMPRESSED, length=current_length)

            if summarize_fn and current_length > self.context_threshold * 1.5:
                success = await self.summarize_and_compress(summarize_fn)
                if success:
                    await self.callbacks.emit(
                        LoopEvent.CONTEXT_SUMMARIZED,
                        new_length=self.calculate_context_length()
                    )
                    return True

            self.compress_context()
            return True

        return False

    def parse_thinking(self, response: str) -> tuple[str, str]:
        """
        Parse thinking content from response.

        Args:
            response: Raw LLM response

        Returns:
            (thinking, response_without_thinking) tuple
        """
        pattern = r'<thinking>(.*?)</thinking>'
        match = re.search(pattern, response, re.DOTALL)

        if match:
            thinking = match.group(1).strip()
            response_without_thinking = re.sub(pattern, '', response, flags=re.DOTALL).strip()
            return thinking, response_without_thinking

        return "", response

    async def verify_result(
        self,
        tool_name: str,
        tool_args: dict,
        tool_result: str,
        verify_fn: Callable[[str, dict, str], Awaitable[tuple[bool, str]]]
    ) -> tuple[bool, str]:
        """
        Verify if a tool result is reasonable.

        Args:
            tool_name: Name of the tool
            tool_args: Arguments passed to the tool
            tool_result: Result from the tool
            verify_fn: Async function that returns (is_valid, reason)

        Returns:
            (is_valid, reason) tuple
        """
        try:
            is_valid, reason = await verify_fn(tool_name, tool_args, tool_result)

            if is_valid:
                logger.debug(f"Verification passed for {tool_name}: {reason}")
                await self.callbacks.emit(
                    LoopEvent.VERIFICATION_PASSED,
                    tool_name=tool_name
                )
            else:
                logger.warning(f"Verification failed for {tool_name}: {reason}")
                await self.callbacks.emit(
                    LoopEvent.VERIFICATION_FAILED,
                    tool_name=tool_name,
                    reason=reason
                )

            return is_valid, reason
        except Exception as e:
            logger.error(f"Verification error: {e}")
            return True, "Verification skipped due to error"

    async def generate_plan(
        self,
        task: str,
        generate_fn: Callable[[str], Awaitable[list[str]]]
    ) -> list[str]:
        """
        Generate an execution plan for a complex task.

        Args:
            task: The task description
            generate_fn: Async function that returns a list of steps

        Returns:
            List of plan steps
        """
        try:
            plan = await generate_fn(task)
            self._current_plan = plan
            logger.info(f"Generated plan with {len(plan)} steps")
            await self.callbacks.emit(LoopEvent.PLAN_GENERATED, plan=plan)
            return plan
        except Exception as e:
            logger.error(f"Failed to generate plan: {e}")
            return []

    async def adjust_plan(
        self,
        current_step: int,
        step_result: str,
        adjust_fn: Callable[[list[str], int, str], Awaitable[list[str]]]
    ) -> list[str]:
        """
        Adjust the current plan based on execution results.

        Args:
            current_step: Current step index
            step_result: Result from current step execution
            adjust_fn: Async function that returns adjusted plan

        Returns:
            Adjusted plan
        """
        try:
            adjusted = await adjust_fn(self._current_plan, current_step, step_result)
            if adjusted != self._current_plan:
                logger.info(f"Plan adjusted: {len(self._current_plan)} -> {len(adjusted)} steps")
                await self.callbacks.emit(
                    LoopEvent.PLAN_ADJUSTED,
                    old_plan=self._current_plan,
                    new_plan=adjusted
                )
            self._current_plan = adjusted
            return adjusted
        except Exception as e:
            logger.error(f"Failed to adjust plan: {e}")
            return self._current_plan

    def get_current_plan(self) -> list[str]:
        """Get the current execution plan."""
        return self._current_plan

    async def start_iteration(self) -> bool:
        """
        Start a new iteration.
        Returns True if iteration can proceed, False if loop should terminate.
        """
        logger.info(f"[AgentLoop.start_iteration] 检查 can_continue()")
        if not self.can_continue():
            reason = self._update_state_on_cannot_continue()
            logger.info(f"[AgentLoop.start_iteration] can_continue()=False, reason={reason}")
            await self._handle_termination()
            return False

        logger.info(f"[AgentLoop.start_iteration] can_continue()=True")

        # Get next work item if source is provided
        if self.work_item_source:
            # Check if we have a failed work item to retry
            if self._failed_work_item and self._retry_count < self._max_retries_per_task:
                self._current_work_item = self._failed_work_item
                # Prepend error context to description
                error_context = f"\n[重试 {self._retry_count + 1}/{self._max_retries_per_task}] 上一步执行失败：{self._failed_work_error}\n请解决上述问题后重试。\n\n"
                self._current_work_item = WorkItem(
                    id=self._failed_work_item.id,
                    description=error_context + self._failed_work_item.description,
                    context=self._failed_work_item.context
                )
                self._retry_count += 1
                logger.info(f"[AgentLoop.start_iteration] Retrying failed work item: id={self._current_work_item.id}, retry={self._retry_count}")
                await self.callbacks.emit(
                    LoopEvent.WORK_ITEM_START,
                    item=self._current_work_item
                )
            else:
                # Get next work item normally
                if self._failed_work_item and self._retry_count >= self._max_retries_per_task:
                    logger.info(f"[AgentLoop.start_iteration] Max retries reached for item {self._failed_work_item.id}, marking as failed and getting next")
                    # Mark as completed with failure, then get next
                    self._failed_work_item = None
                    self._failed_work_error = ""
                    self._retry_count = 0

                self._current_work_item = await self.work_item_source.get_next_work_item()
                logger.info(f"[AgentLoop.start_iteration] get_next_work_item returned: {self._current_work_item}")
                if self._current_work_item is None:
                    logger.info("No more work items")
                    self.context.state.mark_finished("All work items completed")
                    await self._handle_termination()
                    return False
                # Reset retry count for new task
                self._retry_count = 0
                logger.info(f"[AgentLoop.start_iteration] Starting work item: id={self._current_work_item.id}, description={self._current_work_item.description[:50]}...")
                await self.callbacks.emit(
                    LoopEvent.WORK_ITEM_START,
                    item=self._current_work_item
                )

        self.context.state.increment_iteration()
        self._iteration_start_time = time.time()

        logger.info(f"Starting iteration {self.context.state.iteration}/{self.context.state.max_iterations}")

        await self.callbacks.emit(
            LoopEvent.ITERATION_START,
            iteration=self.context.state.iteration
        )

        return True

    async def end_iteration(self, success: bool = True) -> None:
        """End the current iteration."""
        elapsed = time.time() - self._iteration_start_time
        self.metrics.iteration_times.append(elapsed)
        self.metrics.total_iterations = self.context.state.iteration

        logger.info(
            f"Iteration {self.context.state.iteration} completed "
            f"in {elapsed:.2f}s, success={success}"
        )

        await self.callbacks.emit(
            LoopEvent.ITERATION_END,
            iteration=self.context.state.iteration,
            success=success,
            elapsed=elapsed
        )

    async def execute_with_tools(
        self,
        execute_fn: Callable[[], Awaitable[tuple[str, list[dict], Optional[str]]]],
    ) -> tuple[str, list[dict]]:
        """
        Execute a single iteration with tool calling.

        Args:
            execute_fn: Async function that returns (response, tool_calls, stop_reason)
                       stop_reason can be None if not available

        Returns:
            (response, tool_calls) tuple
        """
        logger.info(f"[AgentLoop.execute_with_tools] 调用 start_iteration()")
        if not await self.start_iteration():
            logger.warning(f"[AgentLoop.execute_with_tools] start_iteration() 返回 False，循环应退出")
            return "", []

        logger.info(f"[AgentLoop.execute_with_tools] start_iteration() 返回 True，继续执行")

        try:
            result = await execute_fn()
            if len(result) == 3:
                response, tool_calls, stop_reason = result
            else:
                response, tool_calls = result
                stop_reason = None
            self._last_stop_reason = stop_reason

            # Record tool calls (result will be updated after execution)
            for tc in tool_calls:
                entry = ToolCallEntry(
                    tool_name=tc.get("name", "unknown"),
                    arguments=tc.get("arguments", {}),
                    start_time=time.time(),
                    end_time=time.time(),
                    success=True,
                    result=None  # Will be updated after tool execution
                )
                self.context.state.add_tool_call(entry)
                self.metrics.total_tool_calls += 1
                self.metrics.successful_tool_calls += 1

            # Handle work item completion - defer until after confirmation check
            # Save for completion in run() after confirmation
            pending_item = self._current_work_item
            self._pending_work_item = pending_item
            self._pending_work_response = response
            self._current_work_item = None

            # Emit WORK_ITEM_END event (but don't mark complete yet)
            if pending_item:
                await self.callbacks.emit(
                    LoopEvent.WORK_ITEM_END,
                    item=pending_item,
                    response=response
                )

            await self.end_iteration(success=True)
            logger.info(f"[AgentLoop.execute_with_tools] Returning: response_len={len(response)}, tool_calls={len(tool_calls)}")
            return response, tool_calls

        except Exception as e:
            logger.error(f"Error in iteration {self.context.state.iteration}: {e}")
            self.context.state.mark_error(str(e))
            self.metrics.failed_tool_calls += 1

            # Don't mark work item as completed on error - let caller handle retry/failure
            # The work item remains in progress so it can be retried or marked failed
            if self._current_work_item:
                await self.callbacks.emit(
                    LoopEvent.WORK_ITEM_END,
                    item=self._current_work_item,
                    response=f"[Error] {str(e)}"
                )
                self._current_work_item = None

            await self.end_iteration(success=False)
            raise

    async def run(
        self,
        execute_fn: Callable[[], Awaitable[tuple[str, list[dict]]]],
    ) -> str:
        """
        Run the agent loop until completion or termination.

        Args:
            execute_fn: Async function that returns (response, tool_calls)

        Returns:
            Final response string
        """
        start_time = time.time()
        response = ""
        tool_calls = []

        logger.info(
            f"Starting agent loop: max_iterations={self.context.state.max_iterations}, "
            f"timeout={self.context.state.timeout_seconds}s"
        )

        iteration_count = 0
        while self.should_continue:
            iteration_count += 1
            logger.info(f"[AgentLoop.run] ====== 循环迭代 {iteration_count} ======")
            logger.info(f"[AgentLoop.run] should_continue={self.should_continue}")
            logger.info(f"[AgentLoop.run] is_finished={self.context.state.is_finished}")
            logger.info(f"[AgentLoop.run] iteration={self.context.state.iteration}/{self.context.state.max_iterations}")
            response, tool_calls = await self.execute_with_tools(execute_fn)
            logger.info(f"[AgentLoop.run] After execute_with_tools: tool_calls={len(tool_calls)}, should_continue={self.should_continue}, last_stop_reason={self._last_stop_reason}")

            # If no tool calls, check if we need confirmation
            if not tool_calls:
                if self._on_confirmation_check:
                    # SubagentRunner-style confirmation check
                    confirmed = await self._on_confirmation_check(response, self._last_stop_reason)
                    logger.info(f"[AgentLoop.run] Confirmation check result: confirmed={confirmed}, should_continue={self.should_continue}")
                    if confirmed:
                        # Mark work item as completed AFTER confirmation succeeds
                        if self._pending_work_item and self.work_item_source:
                            await self.work_item_source.on_work_item_completed(
                                self._pending_work_item, self._pending_work_response
                            )
                            logger.info(f"[AgentLoop.run] Work item marked as completed: {self._pending_work_item.id}")
                        # Clear retry state on success
                        self._failed_work_item = None
                        self._failed_work_error = ""
                        self._retry_count = 0
                        self._pending_work_item = None
                        self._pending_work_response = ""
                        # Don't mark_finished() or break here - continue to next iteration to process more tasks
                        # The loop will exit when get_next_work_item() returns None
                        continue
                    else:
                        # Confirmation failed - store failed work item for retry
                        if self._pending_work_item:
                            self._failed_work_item = self._pending_work_item
                            self._failed_work_error = self._pending_work_response or "任务执行失败"
                            logger.info(f"[AgentLoop.run] Storing failed work item for retry: id={self._failed_work_item.id}, retry_count={self._retry_count}")
                        self._pending_work_item = None
                        self._pending_work_response = ""
                        continue
                else:
                    # No confirmation callback, mark complete and break
                    if self._pending_work_item and self.work_item_source:
                        await self.work_item_source.on_work_item_completed(
                            self._pending_work_item, self._pending_work_response
                        )
                    # Clear retry state
                    self._failed_work_item = None
                    self._failed_work_error = ""
                    self._retry_count = 0
                    self._pending_work_item = None
                    self._pending_work_response = ""
                    self.context.state.mark_finished("Task completed (no more tool calls)")
                    break
            else:
                # Tool calls were made - the task execution cycle completed successfully
                # Mark work item complete and advance to next task
                if self._pending_work_item and self.work_item_source:
                    await self.work_item_source.on_work_item_completed(
                        self._pending_work_item, self._pending_work_response
                    )
                    logger.info(f"[AgentLoop.run] Work item marked completed (tool calls): {self._pending_work_item.id}")
                # Clear retry state and pending work
                self._pending_work_item = None
                self._pending_work_response = ""
                self._failed_work_item = None
                self._failed_work_error = ""
                self._retry_count = 0
                logger.info(f"[AgentLoop.run] Tool calls present, will continue loop")
                continue

        self.metrics.total_time_seconds = time.time() - start_time

        logger.info(
            f"Agent loop finished: iterations={self.metrics.total_iterations}, "
            f"total_time={self.metrics.total_time_seconds:.2f}s, "
            f"termination_reason={self.context.state.termination_reason}"
        )
        logger.info(f"[AgentLoop.run] ====== 循环已退出 ======")
        logger.info(f"[AgentLoop.run] is_finished={self.context.state.is_finished}")
        logger.info(f"[AgentLoop.run] should_continue={self.should_continue}")

        await self._handle_termination()

        return response

    async def _handle_termination(self) -> None:
        """Handle loop termination."""
        reason = self.context.state.termination_reason or "Unknown"
        status = self.context.state.status

        logger.info(
            f"Loop terminated: status={status}, reason={reason}, "
            f"iterations={self.context.state.iteration}"
        )

        await self.callbacks.emit(
            LoopEvent.TERMINATED,
            status=status,
            reason=reason,
            iteration=self.context.state.iteration
        )

    def get_status(self) -> dict:
        """Get current loop status."""
        return {
            "status": self.context.state.status,
            "iteration": self.context.state.iteration,
            "max_iterations": self.context.state.max_iterations,
            "should_terminate": self.context.state.should_terminate,
            "termination_reason": self.context.state.termination_reason,
            "elapsed_seconds": self.context.state.elapsed_seconds,
            "is_timed_out": self.context.state.is_timed_out,
            "metrics": {
                "total_iterations": self.metrics.total_iterations,
                "total_tool_calls": self.metrics.total_tool_calls,
                "successful_tool_calls": self.metrics.successful_tool_calls,
                "failed_tool_calls": self.metrics.failed_tool_calls,
                "total_time_seconds": self.metrics.total_time_seconds,
            }
        }


__all__ = [
    "AgentLoop",
    "LoopEvent",
    "LoopMetrics",
    "LoopCallbacks",
]