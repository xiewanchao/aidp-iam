from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# --- Request models ---

class ResourcePatternIn(BaseModel):
    """A single resource pattern attached to an application."""
    resource_prefix: str = Field(..., examples=["notebook"])
    resource_type: str = Field(..., examples=["notebook"])


class AppCreate(BaseModel):
    app_name: str = Field(..., examples=["jupyter"])
    path_prefix: str = Field(..., examples=["/jupyter"])
    display_name: Optional[str] = None
    description: Optional[str] = None
    resource_patterns: List[ResourcePatternIn] = Field(default_factory=list)


class AppUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


# --- Response models ---

class ResourcePatternResponse(BaseModel):
    app_name: str
    resource_prefix: str
    resource_type: str


class AppResponse(BaseModel):
    app_name: str
    path_prefix: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    resource_patterns: List[ResourcePatternResponse] = Field(default_factory=list)
