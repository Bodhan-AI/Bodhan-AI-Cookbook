from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database.session import Base

class InvestigationNote(Base):
    __tablename__ = "investigation_notes"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(String(50), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
    officer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    officer_name = Column(String(120), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    report = relationship("Report", back_populates="notes")
    officer = relationship("User", back_populates="notes")
