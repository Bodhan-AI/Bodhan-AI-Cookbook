from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.alert import Alert
from app.models.user import User
from app.schemas.alert import AlertOut, AlertStatusUpdate
from app.core.dependencies import get_current_user, require_roles
from app.services.outbreak_service import outbreak_engine

router = APIRouter(prefix="/alerts", tags=["Outbreak Alerts"])

@router.get("", response_model=List[AlertOut])
def get_alerts(
    crop: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns outbreak cluster alerts with mandatory expert verification disclaimer."""
    query = db.query(Alert)
    if crop:
        query = query.filter(Alert.crop_name.ilike(f"%{crop}%"))
    if district:
        query = query.filter(Alert.district.ilike(f"%{district}%"))
    if status:
        query = query.filter(Alert.status == status)
    if severity:
        query = query.filter(Alert.severity == severity)

    alerts = query.order_by(Alert.created_at.desc()).all()
    return alerts

@router.get("/{id}", response_model=AlertOut)
def get_alert_by_id(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    alert = db.query(Alert).filter(Alert.id == id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert

@router.post("/detect", response_model=List[AlertOut])
def trigger_outbreak_detection(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["officer", "admin"]))
):
    """Manually triggers the rule-based outbreak detection engine."""
    alerts = outbreak_engine.run_detection(db)
    return alerts

@router.patch("/{id}/status", response_model=AlertOut)
def update_alert_status(
    id: str,
    status_update: AlertStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["officer", "admin"]))
):
    alert = db.query(Alert).filter(Alert.id == id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.status = status_update.status
    db.commit()
    db.refresh(alert)
    return alert
