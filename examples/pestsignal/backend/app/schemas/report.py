from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

class InvestigationNoteOut(BaseModel):
    id: int
    report_id: str
    officer_id: Optional[int] = None
    officer_name: str
    note: str
    created_at: datetime

    class Config:
        from_attributes = True

class InvestigationNoteCreate(BaseModel):
    note: str = Field(..., min_length=2)

class ReportCreate(BaseModel):
    crop_name: str = Field(..., min_length=1)
    crop_variety: Optional[str] = None
    growth_stage: Optional[str] = None
    symptoms: str = Field(..., description="Comma-separated or JSON list of symptoms")
    custom_symptom: Optional[str] = None
    source_language: str = Field("te")
    original_transcription: str = Field(..., min_length=1)
    english_translation: str = Field(..., min_length=1)
    audio_file_path: Optional[str] = None
    submission_method: str = Field("voice", pattern="^(voice|text)$")
    state: str = Field(..., min_length=1)
    district: str = Field(..., min_length=1)
    village: str = Field(..., min_length=1)
    farm_location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    observation_date: str = Field(...)
    affected_plants: Optional[int] = None

class ReportStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(Submitted|Under Review|Investigation|Resolved|Closed)$")
    internal_note: Optional[str] = None

class ReportOut(BaseModel):
    id: str
    farmer_id: int
    crop_name: str
    crop_variety: Optional[str] = None
    growth_stage: Optional[str] = None
    symptoms: str
    custom_symptom: Optional[str] = None
    source_language: str
    original_transcription: str
    english_translation: str
    audio_file_path: Optional[str] = None
    submission_method: str
    state: str
    district: str
    village: str
    farm_location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    observation_date: str
    affected_plants: Optional[int] = None
    investigation_status: str
    created_at: datetime
    updated_at: datetime
    farmer_name: Optional[str] = None
    notes: List[InvestigationNoteOut] = []

    class Config:
        from_attributes = True
