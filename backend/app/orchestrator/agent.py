"""The JARVIS agent loop: plan → act → observe → verify → report."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PermissionMode, TaskStatus, ToolPermission
from app.core.errors import (
    ApprovalTimeoutError,
    EmergencyStopError,
    JarvisError,
    PermissionDeniedError,
    ProviderError,
)
from app.core.logging import audit, get_logger, redact, redact_payload, redact_value
from app.database.models import Approval, AuditLog, Message, Task, TaskStep, ToolExecution
from app.memory.manager import MemoryManager
from app.orchestrator.planner import create_plan
from app.orchestrator.prompts import system_prompt
from app.providers.base import ChatMessage, LLMProvider, ToolCall
from app.tools.base import ToolContext, ToolResult, ToolRegistry, fail

log = get_logger("orchestrator")

MAX_TOOL_MESSAGE_CHARS = 16_000
MAX_HISTORY_MESSAGES = 24


class AgentOrchestrator:
    def __init__(self, services: Any) -> None:
        self.services = services

    @property
    def db(self):
        return self.services.db

    @property
    def events(self):
        return self.services.events

    @property
    def registry(self) -> ToolRegistry:
        return self.services.registry

    @property
    def permissions(self):
        return self.services.permissions

    @property
    def memory(self) -> MemoryManager:
        return self.services.memory

    async def run(
        self,
        session: AsyncSession,
        *,
        task_id: str,
        instruction: str,
        conversation_id: str | None,
        user_message_id: str | None = None,
        auto_approved: set[str] | None = None,
    ) -> None:
        auto_approved = auto_approved or set()
        app_settings = self.services.settings.app
        task = await session.get(Task, task_id)
        if task is None:
            log.error("Task %s not found", task_id)
            return

        statistics: dict[str, Any] = {
            "tools": 0,
            "succeeded": 0,
            "failed": 0,
            "unverified": 0,
            "llm_calls": 0,
            "llm_ms": 0.0,
            "planner_ms": 0.0,
            "llm": [],
        }
        executions: list[dict[str, Any]] = []
        final_content = ""
        status = TaskStatus.COMPLETED

        try:
            self.permissions.emergency.check()
            task.status = TaskStatus.PLANNING.value
            task.started_at = datetime.now(timezone.utc)
            await session.commit()
            await self.events.publish(
                "task.status",
                {"task_id": task_id, "status": task.status, "instruction": instruction, "conversation_id": conversation_id},
            )

            history = await self._load_history(session, conversation_id, user_message_id)
            memory_context = await self.memory.context_for(session, instruction)

            provider = await self.services.provider_manager.get()
            planner_started = time.perf_counter()
            plan = await create_plan(provider, instruction, self.registry.names(), memory_context)
            statistics["planner_ms"] = round((time.perf_counter() - planner_started) * 1000, 1)
            statistics["plan_steps"] = len(plan.get("steps", []))
            statistics["plan_source"] = plan.get("source", "model")
            statistics["history_messages"] = len(history)
            task.objective = plan["objective"]
            task.plan = plan["steps"]
            task.status = TaskStatus.RUNNING.value
            await session.commit()
            await self.events.publish(
                "task.plan", {"task_id": task_id, "objective": plan["objective"], "steps": plan["steps"]}
            )

            prompt = system_prompt(
                permission_mode=self.services.settings.permission_mode,
                allowed_directories=self.services.settings.allowed_directories,
                memory_context=memory_context,
                browser_running=self.services.browser.is_running,
                auto_approved=auto_approved,
            )
            messages: list[ChatMessage] = [ChatMessage(role="system", content=prompt)]
            messages.extend(history)
            user_content = instruction
            attachments = await self._attachments_for(session, user_message_id)
            if attachments:
                listing = "\n".join(
                    f"- {item.get('name')} ({item.get('mime', 'file')}) at {item.get('path')}" for item in attachments
                )
                user_content += f"\n\nAttached files (use document/file tools to inspect):\n{listing}"
            messages.append(ChatMessage(role="user", content=user_content))

            tool_specs = self.registry.specs()
            planned_tools = {
                name
                for step in plan.get("steps", [])
                if isinstance(step, dict)
                for name in step.get("tools", [])
                if self.registry.get(name) is not None
            }
            if planned_tools:
                # Small local models degrade sharply when given dozens of schemas.
                # Restrict the agent turn to the tools the planner actually chose.
                tool_specs = [
                    spec
                    for spec in tool_specs
                    if spec["function"]["name"] in planned_tools
                ]
            step_index = 0
            no_progress_signature: str | None = None
            no_progress_repeats = 0

            for iteration in range(app_settings.max_steps):
                self.permissions.emergency.check()
                await self.events.publish("agent.thinking", {"task_id": task_id, "iteration": iteration + 1})

                try:
                    llm_started = time.perf_counter()
                    response = await provider.chat(
                        messages,
                        tool_specs,
                        temperature=self.services.settings.llm.get("temperature", 0.2),
                        on_text=_stream_callback(self.events, task_id),
                    )
                    llm_ms = (time.perf_counter() - llm_started) * 1000
                except ProviderError as exc:
                    raise
                except Exception as exc:  # provider boundary
                    raise ProviderError(f"The model provider failed: {exc}") from exc

                usage = dict(response.usage or {})
                statistics["llm_calls"] += 1
                statistics["llm_ms"] = round(statistics["llm_ms"] + llm_ms, 1)
                metrics = {
                    "iteration": iteration + 1,
                    "duration_ms": round(llm_ms, 1),
                    "messages": len(messages),
                    "tools": len(tool_specs),
                    "system_prompt_chars": len(prompt),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "tokens_per_second": usage.get("tokens_per_second"),
                    "first_token_ms": usage.get("first_token_ms"),
                }
                statistics["llm"].append(metrics)
                await self.events.publish("llm.metrics", {"task_id": task_id, **metrics})

                # Small local models occasionally print a tool call as JSON text
                # instead of using native tool calling. Recover it before the
                # content is persisted, so raw JSON never becomes the visible
                # assistant answer while the requested action stays unexecuted.
                if not response.tool_calls:
                    salvaged = self._recover_text_tool_call(response.content)
                    if salvaged is not None:
                        response = response.model_copy(
                            update={"content": "", "tool_calls": [salvaged]}
                        )
                        audit(
                            "tool.recovered",
                            task_id=task_id,
                            tool=salvaged.name,
                            arguments=redact_value(salvaged.arguments),
                        )
                        await self.events.publish(
                            "tool.recovered",
                            {
                                "task_id": task_id,
                                "tool": salvaged.name,
                                "arguments": redact_value(salvaged.arguments),
                            },
                        )
                        log.warning(
                            "Recovered a text-encoded tool call for %s on task %s",
                            salvaged.name,
                            task_id,
                        )

                if response.content.strip():
                    await self._store_assistant_message(session, conversation_id, response.content, task_id)
                    await self.events.publish(
                        "llm.completed", {"task_id": task_id, "content": redact(response.content, 20000)}
                    )

                assistant_message = ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls or None,
                )
                messages.append(assistant_message)

                if not response.tool_calls:
                    final_content = response.content.strip()
                    break

                for call in response.tool_calls:
                    step_index += 1
                    started_at = datetime.now(timezone.utc)
                    tool_def = self.registry.get(call.name)
                    if tool_def is None:
                        # Persist the rejected attempt so history is complete and
                        # the UI can explain why nothing ran.
                        result = fail(
                            f"Unknown tool '{call.name}'.",
                            hint="Use only tools from the provided list.",
                        )
                        execution = ToolExecution(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            step_index=step_index,
                            tool_name=call.name,
                            arguments=redact_value(call.arguments),
                            status="failed",
                            ok=False,
                            verified=False,
                            error=redact(result.error or "", 2000),
                            hint=result.hint,
                            permission="unknown",
                            duration_ms=0.0,
                            started_at=started_at,
                            completed_at=started_at,
                        )
                        session.add(execution)
                        await session.commit()
                        session.add(
                            AuditLog(
                                event="tool.rejected",
                                actor="agent",
                                action=call.name,
                                target=str(redact_value(call.arguments))[:400],
                                outcome="failed",
                                detail={"error": result.error, "reason": "unknown tool"},
                                task_id=task_id,
                            )
                        )
                        await session.commit()
                        await self.events.publish(
                            "tool.completed",
                            {
                                "task_id": task_id,
                                "conversation_id": conversation_id,
                                "execution_id": execution.id,
                                "step": step_index,
                                "tool": call.name,
                                "status": "failed",
                                "ok": False,
                                "verified": False,
                                "evidence": "",
                                "error": result.error,
                                "hint": result.hint,
                                "permission": "unknown",
                                "duration_ms": 0.0,
                            },
                        )
                        statistics["tools"] += 1
                        statistics["failed"] += 1
                        executions.append(
                            {
                                "id": execution.id,
                                "index": step_index,
                                "tool": call.name,
                                "arguments": redact_value(call.arguments),
                                "ok": False,
                                "verified": False,
                                "status": "failed",
                                "evidence": "",
                                "error": redact(result.error or "", 600),
                                "attempts": 0,
                                "duration_ms": 0.0,
                            }
                        )
                        signature = (
                            f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
                        )
                        if signature == no_progress_signature:
                            no_progress_repeats += 1
                        else:
                            no_progress_repeats = 0
                        no_progress_signature = signature
                        if no_progress_repeats >= 3:
                            raise JarvisError(
                                "The agent repeated the same unknown action three times.",
                                hint="The task was stopped to avoid a loop. Retry with a tool from the provided list.",
                            )
                        await self._append_tool_result(messages, call, result)
                        continue

                    step = TaskStep(
                        task_id=task_id,
                        index=step_index,
                        kind="tool",
                        title=f"{call.name}",
                        status="running",
                        detail={"arguments": redact_value(call.arguments)},
                        started_at=started_at,
                    )
                    execution = ToolExecution(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        step_index=step_index,
                        tool_name=call.name,
                        arguments=redact_value(call.arguments),
                        status="running",
                        permission=tool_def.permission.value,
                        started_at=started_at,
                    )
                    session.add(step)
                    session.add(execution)
                    task.current_step = step_index
                    await session.commit()
                    await self.events.publish(
                        "tool.started",
                        {
                            "task_id": task_id,
                            "conversation_id": conversation_id,
                            "execution_id": execution.id,
                            "step": step_index,
                            "tool": call.name,
                            "arguments": redact_value(call.arguments),
                            "permission": tool_def.permission.value,
                            "status": "running",
                        },
                    )

                    decision = await self.permissions.evaluate(tool_def, auto_approved=auto_approved)
                    result: ToolResult
                    duration_ms = 0.0
                    attempts = 0
                    execution_status = "failed"

                    if not decision.allowed:
                        result = fail(decision.reason, hint="Change the request or switch the permission mode in Settings.")
                        step.status = "skipped"
                        execution_status = "skipped"
                    elif decision.requires_approval:
                        execution.status = "pending_approval"
                        await session.commit()
                        await self.events.publish(
                            "tool.status",
                            {
                                "task_id": task_id,
                                "conversation_id": conversation_id,
                                "execution_id": execution.id,
                                "step": step_index,
                                "tool": call.name,
                                "status": "pending_approval",
                            },
                        )
                        try:
                            await self.permissions.request_approval(
                                session,
                                task_id=task_id,
                                tool_name=call.name,
                                arguments=call.arguments,
                                permission=tool_def.permission.value,
                                reason=decision.reason,
                                timeout=app_settings.approval_timeout_seconds,
                            )
                            result, duration_ms, attempts = await self._execute_with_retries(
                                session, call, task_id, conversation_id, auto_approved, attempts
                            )
                            execution_status = "completed" if result.ok else "failed"
                        except PermissionDeniedError as exc:
                            result = fail(exc.message, hint=exc.hint)
                            step.status = "skipped"
                            execution_status = "skipped"
                        except ApprovalTimeoutError as exc:
                            result = fail(exc.message, hint=exc.hint)
                            step.status = "failed"
                            execution_status = "failed"
                    else:
                        result, duration_ms, attempts = await self._execute_with_retries(
                            session, call, task_id, conversation_id, auto_approved, attempts
                        )
                        execution_status = "completed" if result.ok else "failed"

                    elapsed_total = duration_ms
                    if result.ok:
                        statistics["succeeded"] += 1
                        if not result.verified:
                            statistics["unverified"] += 1
                        step.status = "completed"
                        execution_status = "completed"
                    else:
                        statistics["failed"] += 1
                        if step.status not in {"skipped"}:
                            step.status = "failed"
                    statistics["tools"] += 1

                    record = {
                        "id": execution.id,
                        "index": step_index,
                        "tool": call.name,
                        "arguments": redact_value(call.arguments),
                        "ok": result.ok,
                        "verified": result.verified,
                        "status": execution_status,
                        "evidence": redact(result.evidence, 600),
                        "error": redact(result.error or "", 600),
                        "attempts": attempts,
                        "duration_ms": round(elapsed_total, 1),
                    }
                    executions.append(record)

                    completed_at = datetime.now(timezone.utc)
                    step.completed_at = completed_at
                    step.detail = record
                    approval_status = None
                    if decision.requires_approval:
                        approval_status = await self._latest_approval_status(session, task_id, call.name)
                    execution.status = execution_status
                    execution.result = redact_payload(result.model_dump(mode="json"))
                    execution.ok = result.ok
                    execution.verified = result.verified
                    execution.evidence = redact(result.evidence, 2000) or None
                    execution.error = redact(result.error or "", 2000) or None
                    execution.hint = result.hint
                    execution.approval_status = approval_status
                    execution.duration_ms = elapsed_total
                    execution.completed_at = completed_at
                    session.add(
                        AuditLog(
                            event="tool.skipped" if execution_status == "skipped" else "tool.executed",
                            actor="agent",
                            action=call.name,
                            target=str(redact_value(call.arguments))[:400],
                            outcome=(
                                "skipped"
                                if execution_status == "skipped"
                                else "ok" if result.ok else "failed"
                            ),
                            detail=record,
                            task_id=task_id,
                        )
                    )
                    await session.commit()
                    await self.events.publish(
                        "tool.completed",
                        {
                            "task_id": task_id,
                            "conversation_id": conversation_id,
                            "execution_id": execution.id,
                            "step": step_index,
                            "tool": call.name,
                            "status": execution_status,
                            "ok": result.ok,
                            "verified": result.verified,
                            "evidence": redact(result.evidence, 2000),
                            "error": result.error,
                            "hint": result.hint,
                            "artifacts": result.artifacts,
                            "permission": tool_def.permission.value,
                            "approval_status": approval_status,
                            "duration_ms": round(elapsed_total, 1),
                        },
                    )
                    await self._append_tool_result(messages, call, result)

                    signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
                    if not result.ok and signature == no_progress_signature:
                        no_progress_repeats += 1
                    else:
                        no_progress_repeats = 0
                    no_progress_signature = signature
                    if no_progress_repeats >= 3:
                        raise JarvisError(
                            "The agent repeated the same failing action three times without progress.",
                            hint="The task was stopped to avoid a loop. Inspect the last error and try a different approach.",
                        )

            else:
                status = TaskStatus.BLOCKED
                final_content = ""
                task.error = f"Reached the maximum of {app_settings.max_steps} steps without completing the objective."

            # Never report success when a required action failed, even if the
            # model's final text claims otherwise. An execution record only stays
            # failed when retries were exhausted or authorization was refused.
            unresolved = [item for item in executions if not item["ok"]]
            if status == TaskStatus.COMPLETED and unresolved:
                status = TaskStatus.FAILED
                final_content = ""
                failed_tools = ", ".join(sorted({str(item["tool"]) for item in unresolved}))
                task.error = (
                    f"Task not reported as complete: {len(unresolved)} action(s) failed "
                    f"({failed_tools}). Review the execution evidence and retry with corrected "
                    "parameters."
                )

            if status != TaskStatus.COMPLETED:
                await self._finalize_failure(
                    session,
                    task_id,
                    status=status,
                    error=task.error or "The task stopped before completion.",
                    conversation_id=conversation_id,
                    executions=executions,
                    statistics=statistics,
                )
                return

            if status == TaskStatus.COMPLETED:
                if not final_content:
                    if statistics["succeeded"] == 0 and statistics["failed"] > 0:
                        status = TaskStatus.FAILED
                        final_content = "The task did not complete: every attempted action failed. See the execution evidence below."
                    else:
                        final_content = "The task finished, but the model produced no final summary."
                final_content = self._with_evidence(final_content, executions, statistics)
                task.result = redact(final_content, 60000)
                task.status = TaskStatus.COMPLETED.value
                task.completed_at = datetime.now(timezone.utc)
                task.metadata_json = {**task.metadata_json, "statistics": statistics, "executions": executions}
                await session.commit()
                await self.events.publish(
                    "task.completed",
                    {
                        "task_id": task_id,
                        "conversation_id": conversation_id,
                        "status": task.status,
                        "result": task.result,
                        "statistics": statistics,
                        "executions": executions,
                    },
                )
                audit("task.completed", task_id=task_id, status=task.status, statistics=statistics)

        except EmergencyStopError as exc:
            await self._finalize_failure(
                session, task_id, status=TaskStatus.CANCELLED, error=str(exc), conversation_id=conversation_id
            )
        except asyncio.CancelledError:
            await self._finalize_cancelled(session, task_id, conversation_id)
            raise
        except JarvisError as exc:
            await self._finalize_failure(
                session,
                task_id,
                status=TaskStatus.FAILED if status != TaskStatus.BLOCKED else status,
                error=exc.message,
                hint=exc.hint,
                conversation_id=conversation_id,
                executions=executions,
                statistics=statistics,
            )
        except Exception as exc:  # noqa: BLE001 - top-level agent safety net
            log.exception("Task %s crashed", task_id)
            await self._finalize_failure(
                session,
                task_id,
                status=TaskStatus.FAILED,
                error=f"{exc.__class__.__name__}: {exc}",
                conversation_id=conversation_id,
                executions=executions,
                statistics=statistics,
            )
        finally:
            await self.events.publish("agent.idle", {"task_id": task_id})

    async def _execute_with_retries(
        self,
        session: AsyncSession,
        call: ToolCall,
        task_id: str,
        conversation_id: str | None,
        auto_approved: set[str],
        attempts: int,
    ) -> tuple[ToolResult, float, int]:
        ctx = ToolContext(
            session=session,
            task_id=task_id,
            conversation_id=conversation_id,
            services=self.services,
            auto_approved=auto_approved,
        )
        total_duration = 0.0
        result: ToolResult
        max_retries = self.services.settings.app.max_retries
        while True:
            result, duration = await self.registry.execute(call.name, call.arguments, ctx)
            attempts += 1
            total_duration += duration
            if result.ok or not result.retryable or attempts > max_retries:
                return result, total_duration, attempts
            await self.events.publish(
                "tool.retry",
                {"task_id": task_id, "tool": call.name, "attempt": attempts, "error": result.error},
            )
            await asyncio.sleep(min(4.0, 0.8 * attempts))

    _TEXT_TOOL_CALL_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)

    def _recover_text_tool_call(self, content: str) -> ToolCall | None:
        """Parse a tool call that the model wrote as plain JSON text.

        Only an exact JSON object that names a registered tool qualifies, so a
        normal JSON answer is never mistaken for an action.
        """
        text = (content or "").strip()
        if not text:
            return None
        fenced = self._TEXT_TOOL_CALL_FENCE.fullmatch(text)
        if fenced:
            text = fenced.group(1).strip()
        if not (text.startswith("{") and text.endswith("}")):
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return None
        if self.registry.get(name.strip()) is None:
            return None
        call_id = payload.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"call_recovered_{uuid.uuid4().hex[:12]}"
        return ToolCall(id=call_id, name=name.strip(), arguments=arguments)

    async def _append_tool_result(self, messages: list[ChatMessage], call: ToolCall, result: ToolResult) -> None:
        payload = result.as_message()
        if len(payload) > MAX_TOOL_MESSAGE_CHARS:
            payload = payload[:MAX_TOOL_MESSAGE_CHARS] + "... [truncated]"
        messages.append(
            ChatMessage(role="tool", content=payload, tool_call_id=call.id, name=call.name)
        )

    async def _store_assistant_message(
        self,
        session: AsyncSession,
        conversation_id: str | None,
        content: str,
        task_id: str | None = None,
    ) -> None:
        if not conversation_id:
            return
        message = Message(
            conversation_id=conversation_id,
            task_id=task_id,
            role="assistant",
            content=content,
        )
        session.add(message)
        await session.flush()
        await self.memory.index_message(session, message)
        await session.commit()

    async def _latest_approval_status(
        self, session: AsyncSession, task_id: str, tool_name: str
    ) -> str | None:
        approval = (
            await session.execute(
                select(Approval)
                .where(Approval.task_id == task_id, Approval.tool_name == tool_name)
                .order_by(Approval.requested_at.desc())
                .limit(1)
            )
        ).scalars().first()
        return approval.status if approval is not None else None

    async def _close_open_executions(
        self, session: AsyncSession, task_id: str, *, status: str, error: str
    ) -> None:
        rows = (
            await session.execute(
                select(ToolExecution).where(
                    ToolExecution.task_id == task_id,
                    ToolExecution.status.in_(["running", "pending_approval"]),
                )
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = status
            row.completed_at = now
            row.error = row.error or redact(error, 2000)

    async def _load_history(
        self, session: AsyncSession, conversation_id: str | None, user_message_id: str | None
    ) -> list[ChatMessage]:
        if not conversation_id:
            return []
        rows = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.role.in_(["user", "assistant"]))
                .order_by(Message.created_at.asc())
            )
        ).scalars().all()
        if user_message_id:
            for index, message in enumerate(rows):
                if message.id == user_message_id:
                    rows = rows[:index]
                    break
        history = []
        for message in rows[-MAX_HISTORY_MESSAGES:]:
            if message.content.strip():
                history.append(ChatMessage(role=message.role, content=message.content[:6000]))
        return history

    async def _attachments_for(self, session: AsyncSession, user_message_id: str | None) -> list[dict[str, Any]]:
        if not user_message_id:
            return []
        message = await session.get(Message, user_message_id)
        return list(message.attachments or []) if message else []

    def _with_evidence(self, content: str, executions: list[dict[str, Any]], statistics: dict[str, Any]) -> str:
        if not executions:
            return content
        lines = [
            "",
            "---",
            f"**Execution evidence** — {statistics['succeeded']} succeeded, "
            f"{statistics['failed']} failed, {statistics['unverified']} unverified of {statistics['tools']} action(s).",
        ]
        for item in executions[-12:]:
            marker = "✓" if item["ok"] and item["verified"] else ("!" if item["ok"] else "✗")
            detail = item["evidence"] or item["error"] or ""
            lines.append(f"- {marker} `{item['tool']}` — {detail[:260]}")
        return content.rstrip() + "\n" + "\n".join(lines)

    async def _finalize_cancelled(self, session: AsyncSession, task_id: str, conversation_id: str | None) -> None:
        try:
            await session.rollback()
            task = await session.get(Task, task_id)
            if task is not None:
                task.status = TaskStatus.CANCELLED.value
                task.cancelled_at = datetime.now(timezone.utc)
                task.error = "Cancelled by the user."
            await self._close_open_executions(
                session, task_id, status="cancelled", error="Cancelled by the user."
            )
            await session.commit()
            await self.permissions.cancel_task_approvals(session, task_id)
            await self.events.publish(
                "task.cancelled", {"task_id": task_id, "conversation_id": conversation_id, "status": "cancelled"}
            )
            audit("task.cancelled", task_id=task_id)
        except Exception as exc:  # pragma: no cover
            log.warning("Could not persist cancellation for %s: %s", task_id, exc)

    async def _finalize_failure(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        status: TaskStatus,
        error: str,
        conversation_id: str | None,
        hint: str = "",
        executions: list[dict[str, Any]] | None = None,
        statistics: dict[str, Any] | None = None,
    ) -> None:
        try:
            await session.rollback()
            task = await session.get(Task, task_id)
            if task is not None:
                task.status = status.value
                task.error = redact(error, 5000)
                task.completed_at = datetime.now(timezone.utc)
                report = f"Task {status.value}: {error}"
                if hint:
                    report += f"\n\nHint: {hint}"
                if executions:
                    report = self._with_evidence(report, executions, statistics or {})
                task.result = report
                if statistics is not None:
                    task.metadata_json = {
                        **(task.metadata_json or {}),
                        "statistics": statistics,
                        "executions": executions or [],
                    }
            await self._close_open_executions(
                session,
                task_id,
                status="cancelled" if status == TaskStatus.CANCELLED else "interrupted",
                error=error,
            )
            await session.commit()
            await self.permissions.cancel_task_approvals(session, task_id)
            await self.events.publish(
                "task.failed" if status == TaskStatus.FAILED else "task.cancelled",
                {
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "status": status.value,
                    "error": redact(error, 2000),
                    "hint": hint,
                },
            )
            audit("task.finished", task_id=task_id, status=status.value, error=redact(error, 1000))
        except Exception as exc:  # pragma: no cover
            log.warning("Could not persist task failure for %s: %s", task_id, exc)

    # Session is passed explicitly to every persistence helper.


def _stream_callback(events: Any, task_id: str):
    async def publish(chunk: str) -> None:
        await events.publish("llm.delta", {"task_id": task_id, "delta": chunk})

    return publish
