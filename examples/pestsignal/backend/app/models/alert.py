from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from app.database.session import Base

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String(50), primary_key=True, index=True) # e.g. "ALT-2026-001"
    crop_name = Column(String(100), nullable=False, index=True)
    state = Column(String(100), nullable=False, index=True)
    district = Column(String(100), nullable=False, index=True)
    symptoms = Column(String(255), nullable=False)
    matching_report_count = Column(Integer, nullable=False, default=1)
    severity = Column(String(20), nullable=False, default="Low") # "Low", "Medium", "High"
    detection_period = Column(String(50), nullable=False, default="7 days")
    status = Column(String(30), nullable=False, default="Active") # "Active", "Investigating", "Resolved", "Dismissed"
    disclaimer: str = "Potential outbreak signal – expert verification required."
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
