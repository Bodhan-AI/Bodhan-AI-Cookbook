import secrets
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.user import User
from app.models.report import Report
from app.models.note import InvestigationNote
from app.models.notification import Notification
from app.schemas.report import ReportCreate, ReportOut, ReportStatusUpdate, InvestigationNoteCreate, InvestigationNoteOut
from app.core.dependencies import get_current_user, require_roles
from app.services.outbreak_service import outbreak_engine

router = APIRouter(prefix="/reports", tags=["Crop Reports"])

def generate_report_id() -> str:
    year = datetime.utcnow().year
    num = secrets.randbelow(9000) + 1000
    return f"PS-{year}-{num}"

@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    report_in: ReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Submits a new crop disease or pest infestation report.
    Farmers submit their reports, which are saved in the central database.
    """
    report_id = generate_report_id()
    
    # Ensure ID uniqueness
    while db.query(Report).filter(Report.id == report_id).first():
        report_id = generate_report_id()

    report = Report(
        id=report_id,
        farmer_id=current_user.id,
        crop_name=report_in.crop_name.strip().title(),
        crop_variety=report_in.crop_variety.strip() if report_in.crop_variety else None,
        growth_stage=report_in.growth_stage.strip() if report_in.growth_stage else None,
        symptoms=report_in.symptoms.strip(),
        custom_symptom=report_in.custom_symptom.strip() if report_in.custom_symptom else None,
        source_language=report_in.source_language,
        original_transcription=report_in.original_transcription.strip(),
        english_translation=report_in.english_translation.strip(),
        audio_file_path=report_in.audio_file_path,
        submission_method=report_in.submission_method,
        state=report_in.state.strip(),
        district=report_in.district.strip(),
        village=report_in.village.strip(),
        farm_location=report_in.farm_location.strip() if report_in.farm_location else None,
        latitude=report_in.latitude,
        longitude=report_in.longitude,
        observation_date=report_in.observation_date,
        affected_plants=report_in.affected_plants,
        investigation_status="Submitted"
    )

    db.add(report)
    db.flush()

    # Create submission notification for the farmer
    notif = Notification(
        user_id=current_user.id,
        title="Report Submitted Successfully",
        message=f"Your report {report.id} for {report.crop_name} in {report.district} has been logged.",
        type="report",
        link=f"/farmer/reports/{report.id}",
        created_at=datetime.utcnow()
    )
    db.add(notif)

    # Notify Officers/Admins
    officers = db.query(User).filter(User.role.in_(["officer", "admin"])).all()
    for off in officers:
        db.add(Notification(
            user_id=off.id,
            title=f"New Crop Report: {report.crop_name}",
            message=f"Farmer {current_user.full_name} submitted report {report.id} ({report.district}, {report.state}).",
            type="report",
            link=f"/admin/reports/{report.id}",
            created_at=datetime.utcnow()
        ))

    db.commit()
    db.refresh(report)

    # Trigger Outbreak Detection in background/inline
    try:
        outbreak_engine.run_detection(db)
    except Exception as e:
        # Don't fail report creation if outbreak engine experiences error
        pass

    # Attach farmer_name for schema serialization
    report.farmer_name = current_user.full_name
    return report

@router.get("", response_model=List[ReportOut])
def get_reports(
    crop: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieves reports.
    Farmers only see their own submitted reports.
    Officers and Admins have access to all reports.
    """
    query = db.query(Report)

    if current_user.role == "farmer":
        query = query.filter(Report.farmer_id == current_user.id)

    if crop:
        query = query.filter(Report.crop_name.ilike(f"%{crop}%"))
    if district:
        query = query.filter(Report.district.ilike(f"%{district}%"))
    if state:
        query = query.filter(Report.state.ilike(f"%{state}%"))
    if status:
        query = query.filter(Report.investigation_status == status)
    if language:
        query = query.filter(Report.source_language == language)
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (Report.id.ilike(search_filter)) |
            (Report.crop_name.ilike(search_filter)) |
            (Report.symptoms.ilike(search_filter)) |
            (Report.english_translation.ilike(search_filter)) |
            (Report.district.ilike(search_filter))
        )

    reports = query.order_by(Report.created_at.desc()).all()

    # Preload farmer names
    for r in reports:
        r.farmer_name = r.farmer.full_name if r.farmer else "Unknown Farmer"

    return reports

@router.get("/{id}", response_model=ReportOut)
def get_report_by_id(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns single report details.
    Enforces privacy: farmers cannot access other farmers' reports.
    """
    report = db.query(Report).filter(Report.id == id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if current_user.role == "farmer" and report.farmer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: You can only view your own reports."
        )

    report.farmer_name = report.farmer.full_name if report.farmer else "Unknown Farmer"
    return report

@router.patch("/{id}/status", response_model=ReportOut)
def update_report_status(
    id: str,
    status_update: ReportStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["officer", "admin"]))
):
    """
    Allows Agricultural Officers & Admins to update report status
    (Submitted -> Under Review -> Investigation -> Resolved -> Closed)
    and append an optional internal note.
    """
    report = db.query(Report).filter(Report.id == id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    old_status = report.investigation_status
    report.investigation_status = status_update.status
    report.updated_at = datetime.utcnow()

    # If an internal note was provided, save it
    if status_update.internal_note and status_update.internal_note.strip():
        note = InvestigationNote(
            report_id=report.id,
            officer_id=current_user.id,
            officer_name=current_user.full_name,
            note=status_update.internal_note.strip(),
            created_at=datetime.utcnow()
        )
        db.add(note)

    # Notify the farmer
    farmer_notif = Notification(
        user_id=report.farmer_id,
        title=f"Report Status Update: {report.crop_name}",
        message=f"Your report {report.id} was updated from '{old_status}' to '{status_update.status}' by {current_user.full_name}.",
        type="report",
        link=f"/farmer/reports/{report.id}",
        created_at=datetime.utcnow()
    )
    db.add(farmer_notif)

    db.commit()
    db.refresh(report)
    report.farmer_name = report.farmer.full_name if report.farmer else "Unknown Farmer"
    return report

@router.post("/{id}/notes", response_model=InvestigationNoteOut, status_code=status.HTTP_201_CREATED)
def add_investigation_note(
    id: str,
    note_in: InvestigationNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["officer", "admin"]))
):
    """Adds an internal investigation note to a report."""
    report = db.query(Report).filter(Report.id == id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    note = InvestigationNote(
        report_id=report.id,
        officer_id=current_user.id,
        officer_name=current_user.full_name,
        note=note_in.note.strip(),
        created_at=datetime.utcnow()
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note
