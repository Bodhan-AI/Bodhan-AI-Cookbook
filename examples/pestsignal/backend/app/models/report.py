from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database.session import Base

class Report(Base):
    __tablename__ = "reports"

    id = Column(String(50), primary_key=True, index=True) # e.g. "PS-2026-1001"
    farmer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_name = Column(String(100), nullable=False, index=True)
    crop_variety = Column(String(100), nullable=True)
    growth_stage = Column(String(100), nullable=True)
    symptoms = Column(Text, nullable=False) # JSON array or comma separated
    custom_symptom = Column(Text, nullable=True)
    source_language = Column(String(20), nullable=False, default="te")
    original_transcription = Column(Text, nullable=False)
    english_translation = Column(Text, nullable=False)
    audio_file_path = Column(String(255), nullable=True)
    submission_method = Column(String(20), nullable=False, default="voice") # "voice", "text"
    
    state = Column(String(100), nullable=False, index=True)
    district = Column(String(100), nullable=False, index=True)
    village = Column(String(100), nullable=False)
    farm_location = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    observation_date = Column(String(50), nullable=False)
    affected_plants = Column(Integer, nullable=True)
    investigation_status = Column(String(30), nullable=False, default="Submitted", index=True) 
    # Statuses: Submitted, Under Review, Investigation, Resolved, Closed
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    farmer = relationship("User", back_populates="reports")
    notes = relationship("InvestigationNote", back_populates="report", cascade="all, delete-orphan", order_by="desc(InvestigationNote.created_at)")
