"""Pydantic schemas for AI chatbot orchestration."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatContext(BaseModel):
    """Contextual metadata passed alongside the chat message."""

    model_config = ConfigDict(extra="ignore")

    project_id: UUID | None = Field(
        default=None,
        description="Optional current project UUID to anchor the conversation context.",
    )


class ChatRequest(BaseModel):
    """Incoming user chat message."""

    model_config = ConfigDict(extra="ignore")

    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="The natural language prompt or instruction from the user.",
    )
    context: ChatContext | None = Field(
        default=None,
        description="Optional context including active project_id.",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional client conversation identifier.",
    )


class ProposedTask(BaseModel):
    """Normalized representation of a task proposed by the AI for confirmation."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    project_name: str | None = None
    title: str
    description: str = ""
    type: str = "task"
    priority: str = "medium"
    story_points: int = 0
    assignee_id: str | None = None
    assignee_name: str | None = None
    reporter_id: str | None = None
    reporter_name: str | None = None
    sprint_id: str | None = None
    sprint_name: str | None = None
    user_story_id: str | None = None
    due_date: str | None = None


class PendingAction(BaseModel):
    """Pending confirmation payload stored on the server."""

    model_config = ConfigDict(extra="ignore")

    action_id: str = Field(description="Unique action token required for confirmation.")
    action_type: Literal["create_task"] = "create_task"
    project_id: str
    proposed_task: ProposedTask
    expires_at: str


class CreatedTaskResult(BaseModel):
    """Details of a successfully created task."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    key: str
    title: str
    status: str
    project_id: str
    type: str
    priority: str
    assignee_id: str | None = None
    reporter_id: str | None = None


class ChatResponseData(BaseModel):
    """Payload returned in the data field of the chat response envelope."""

    model_config = ConfigDict(extra="ignore")

    conversation_id: str
    message: str
    action_state: Literal["NONE", "REQUIRES_CONFIRMATION", "COMPLETED", "ERROR"] = "NONE"
    pending_action: PendingAction | None = None
    task: CreatedTaskResult | None = None


class ActionConfirmationRequest(BaseModel):
    """Payload to confirm or reject a pending action."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    confirmed: bool = Field(
        default=True,
        description="Set to true to execute the pending task creation, false to cancel.",
    )
    confirm: bool | None = Field(
        default=None,
        description="Alias for confirmed (pass true to confirm, false to cancel).",
    )

    @property
    def is_confirmed(self) -> bool:
        if self.confirm is not None:
            return self.confirm
        return self.confirmed


class ActionConfirmationResponseData(BaseModel):
    """Payload returned after confirming or cancelling an action."""

    model_config = ConfigDict(extra="ignore")

    action_id: str
    status: Literal["completed", "cancelled"]
    message: str
    task: CreatedTaskResult | None = None
