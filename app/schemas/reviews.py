# Module 38: Product Reviews and Ratings - schemas
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    product_id: int = Field(gt=0)
    # optional, if empty we pick the latest delivered order that has this product
    order_id: Optional[int] = Field(None, gt=0)
    rating: int = Field(ge=1, le=5)
    title: Optional[str] = Field(None, max_length=150)
    comment: Optional[str] = Field(None, max_length=2000)


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    title: Optional[str] = Field(None, max_length=150)
    comment: Optional[str] = Field(None, max_length=2000)


class ReviewModerate(BaseModel):
    action: Literal["approve", "reject", "hide", "remove"]
    note: Optional[str] = Field(None, max_length=255)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    user_id: int
    order_id: int
    rating: int
    title: Optional[str] = None
    comment: Optional[str] = None
    status: Optional[str] = None
    moderated_by: Optional[int] = None
    moderation_note: Optional[str] = None
    created_at: Optional[datetime] = None
