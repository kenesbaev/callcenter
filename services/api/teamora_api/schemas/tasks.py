from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from teamora_api.enums import TaskEventType, TaskPriority, TaskSource, TaskStatus, TaskType


class TaskCreate(BaseModel):
    project_id: UUID
    customer_id: UUID
    task_type: TaskType = TaskType.MANUAL
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=8000)
    priority: TaskPriority = TaskPriority.NORMAL
    call_id: UUID | None = None
    call_outcome_id: UUID | None = None
    assigned_user_id: UUID | None = None
    due_at: datetime
    comment: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_aware_due_at(self) -> TaskCreate:
        if self.due_at.tzinfo is None:
            raise ValueError("Дата выполнения должна содержать часовой пояс")
        return self


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=8000)
    priority: TaskPriority | None = None
    comment: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def reject_empty_update(self) -> TaskUpdate:
        if not self.model_fields_set:
            raise ValueError("Передайте хотя бы одно изменение")
        return self


class TaskCancelRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)


class TaskRescheduleRequest(BaseModel):
    due_at: datetime

    @model_validator(mode="after")
    def require_aware_due_at(self) -> TaskRescheduleRequest:
        if self.due_at.tzinfo is None:
            raise ValueError("Дата выполнения должна содержать часовой пояс")
        return self


class TaskReassignRequest(BaseModel):
    assigned_user_id: UUID | None


class TaskRead(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID
    project_name: str
    project_timezone: str
    task_type: TaskType
    title: str
    description: str
    priority: TaskPriority
    status: TaskStatus
    customer_id: UUID
    customer_name: str | None
    customer_phone: str | None
    call_id: UUID | None
    call_outcome_id: UUID | None
    assigned_user_id: UUID | None
    assigned_user_name: str | None
    created_by_user_id: UUID
    created_by_user_name: str
    due_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    comment: str
    source: TaskSource
    is_overdue: bool
    created_at: datetime
    updated_at: datetime


class TaskEventRead(BaseModel):
    id: UUID
    task_id: UUID
    event_type: TaskEventType
    actor_user_id: UUID | None
    actor_name: str | None
    safe_snapshot: dict[str, object]
    created_at: datetime


class TaskOperatorOption(BaseModel):
    user_id: UUID
    display_name: str


class TaskCustomerOption(BaseModel):
    id: UUID
    display_name: str | None
    phone: str | None


class TaskOptions(BaseModel):
    operators: list[TaskOperatorOption]
    customers: list[TaskCustomerOption]


TaskPeriod = Literal["overdue", "today", "future", "completed"]
