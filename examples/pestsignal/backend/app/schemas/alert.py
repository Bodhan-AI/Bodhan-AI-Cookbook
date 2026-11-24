from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class AlertOut(BaseModel):
    id: str
    crop_name: str
    state: str
    district: str
    symptoms: str
    matching_report_count: int
    severity: str
    detection_period: str
    status: str
    disclaimer: str = "Potential outbreak signal – expert verification required."
    created_at: datetime

    class Config:
        from_attributes = True

class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(Active|Investigating|Resolved|Dismissed)$")
