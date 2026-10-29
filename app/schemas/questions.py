# Module 39: Product Questions and Answers - schemas
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class QuestionCreate(BaseModel):
    product_id: int = Field(gt=0)
    question: str = Field(min_length=10, max_length=1000)


class AnswerCreate(BaseModel):
    answer: str = Field(min_length=5, max_length=2000)


class QuestionClose(BaseModel):
    note: Optional[str] = Field(None, max_length=255)


class Moderate(BaseModel):
    action: Literal["hide", "remove", "restore"]
    reason: str = Field(min_length=5, max_length=255)


class AnswerOut(BaseModel):
    id: int
    answer: str
    answered_by: Optional[int] = None
    answered_by_name: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class QuestionOut(BaseModel):
    id: int
    product_id: int
    user_id: int
    asked_by: Optional[str] = None
    question: str
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    answers: List[AnswerOut] = []
