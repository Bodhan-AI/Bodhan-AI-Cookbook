from datetime import datetime, timedelta
from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database.session import get_db
from app.models.report import Report
from app.models.alert import Alert
from app.models.user import User
from app.core.dependencies import get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics & Reporting Intelligence"])

@router.get("/overview")
def get_analytics_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns high-level statistics calculated strictly from database records.
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    total_reports = db.query(Report).count()
    reports_today = db.query(Report).filter(Report.created_at >= today_start).count()
    under_investigation = db.query(Report).filter(Report.investigation_status.in_(["Under Review", "Investigation"])).count()
    reports_resolved = db.query(Report).filter(Report.investigation_status.in_(["Resolved", "Closed"])).count()
    active_alerts = db.query(Alert).filter(Alert.status == "Active").count()
    total_farmers = db.query(User).filter(User.role == "farmer").count()

    return {
        "total_reports": total_reports,
        "reports_today": reports_today,
        "reports_under_investigation": under_investigation,
        "reports_resolved": reports_resolved,
        "active_alerts": active_alerts,
        "total_farmers": total_farmers
    }

@router.get("/crops")
def get_crop_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Reports grouped by crop name."""
    results = db.query(
        Report.crop_name,
        func.count(Report.id).label("count")
    ).group_by(Report.crop_name).order_by(func.count(Report.id).desc()).all()

    return [{"crop": crop, "count": count} for crop, count in results]

@router.get("/regions")
def get_region_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Reports grouped by district and state."""
    results = db.query(
        Report.district,
        Report.state,
        func.count(Report.id).label("count")
    ).group_by(Report.district, Report.state).order_by(func.count(Report.id).desc()).all()

    return [{"district": d, "state": s, "count": c} for d, s, c in results]

@router.get("/languages")
def get_language_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Reports grouped by source language."""
    from app.services.bodhan_service import SUPPORTED_LANGUAGES
    results = db.query(
        Report.source_language,
        func.count(Report.id).label("count")
    ).group_by(Report.source_language).order_by(func.count(Report.id).desc()).all()

    return [
        {
            "code": lang,
            "language": SUPPORTED_LANGUAGES.get(lang, lang.upper()),
            "count": count
        }
        for lang, count in results
    ]

@router.get("/symptoms")
def get_symptom_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Calculates most frequently observed symptoms across all reports."""
    reports = db.query(Report.symptoms).all()
    counts: Dict[str, int] = {}
    for (s_str,) in reports:
        if not s_str:
            continue
        cleaned = s_str.replace("[", "").replace("]", "").replace('"', '')
        for part in cleaned.split(","):
            s = part.strip()
            if s:
                counts[s] = counts.get(s, 0) + 1

    sorted_symptoms = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [{"symptom": s, "count": c} for s, c in sorted_symptoms]

@router.get("/timeline")
def get_timeline_analytics(
    days: int = 14,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns daily report volume over the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    reports = db.query(Report.created_at).filter(Report.created_at >= cutoff).all()

    # Aggregate by date string
    day_counts: Dict[str, int] = {}
    for i in range(days):
        d_str = (datetime.utcnow() - timedelta(days=i)).strftime("%b %d")
        day_counts[d_str] = 0

    for (created_at,) in reports:
        d_str = created_at.strftime("%b %d")
        if d_str in day_counts:
            day_counts[d_str] += 1

    timeline = [{"date": d, "count": count} for d, count in reversed(list(day_counts.items()))]
    return timeline
