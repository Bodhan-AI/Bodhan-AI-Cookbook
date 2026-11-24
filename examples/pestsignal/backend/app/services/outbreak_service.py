import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.models.report import Report
from app.models.alert import Alert
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)

class OutbreakMonitoringEngine:
    def __init__(self, time_window_days: int = 14, min_cluster_threshold: int = 3):
        self.time_window_days = time_window_days
        self.min_cluster_threshold = min_cluster_threshold

    def run_detection(self, db: Session) -> List[Alert]:
        """
        Runs rule-based outbreak cluster detection on recent reports.
        1. Filters reports submitted within time_window_days.
        2. Groups by (crop_name, state, district).
        3. Identifies recurring symptoms.
        4. Calculates severity:
           - >= 7 reports -> High
           - >= 5 reports -> Medium
           - >= 3 reports -> Low
        5. Creates or updates Alert records with the required expert verification disclaimer.
        6. Generates notifications for Agricultural Officers & Admins.
        """
        cutoff_date = datetime.utcnow() - timedelta(days=self.time_window_days)
        recent_reports = db.query(Report).filter(Report.created_at >= cutoff_date).all()

        if not recent_reports:
            return []

        # Group reports by (crop_name.lower(), state.lower(), district.lower())
        clusters: Dict[tuple, List[Report]] = {}
        for r in recent_reports:
            key = (r.crop_name.strip().title(), r.state.strip().title(), r.district.strip().title())
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(r)

        generated_alerts: List[Alert] = []

        for (crop, state, district), group in clusters.items():
            if len(group) < self.min_cluster_threshold:
                continue

            # Analyze symptom frequency in this cluster
            symptom_counts: Dict[str, int] = {}
            for r in group:
                # symptoms stored as comma-separated or json string
                sym_list = [s.strip() for s in r.symptoms.replace("[", "").replace("]", "").replace('"', '').split(",") if s.strip()]
                for s in sym_list:
                    symptom_counts[s] = symptom_counts.get(s, 0) + 1

            # Top symptom or combined
            top_symptoms = sorted(symptom_counts.items(), key=lambda x: x[1], reverse=True)
            top_symptom_str = ", ".join([f"{s} ({c})" for s, c in top_symptoms[:3]]) if top_symptoms else "General leaf/stem distress"

            report_count = len(group)
            if report_count >= 7:
                severity = "High"
            elif report_count >= 5:
                severity = "Medium"
            else:
                severity = "Low"

            # Check if an active alert for this crop & district already exists
            existing_alert = db.query(Alert).filter(
                Alert.crop_name == crop,
                Alert.state == state,
                Alert.district == district,
                Alert.status.in_(["Active", "Investigating"])
            ).first()

            if existing_alert:
                # Update existing alert count and severity
                existing_alert.matching_report_count = report_count
                existing_alert.severity = severity
                existing_alert.symptoms = top_symptom_str
                generated_alerts.append(existing_alert)
            else:
                alert_id = f"ALT-{datetime.utcnow().year}-{secrets_random_id()}"
                new_alert = Alert(
                    id=alert_id,
                    crop_name=crop,
                    state=state,
                    district=district,
                    symptoms=top_symptom_str,
                    matching_report_count=report_count,
                    severity=severity,
                    detection_period=f"{self.time_window_days} days",
                    status="Active",
                    created_at=datetime.utcnow()
                )
                db.add(new_alert)
                db.flush()
                generated_alerts.append(new_alert)

                # Send notifications to all agricultural officers & admins
                officers_and_admins = db.query(User).filter(User.role.in_(["officer", "admin"])).all()
                for officer in officers_and_admins:
                    notif = Notification(
                        user_id=officer.id,
                        title=f"Potential Outbreak Cluster: {crop} in {district}",
                        message=f"{report_count} reports of {crop} with {top_symptom_str} detected in {district}, {state}. Expert verification required.",
                        type="alert",
                        link=f"/admin/outbreaks",
                        created_at=datetime.utcnow()
                    )
                    db.add(notif)

        db.commit()
        return generated_alerts

def secrets_random_id() -> str:
    import secrets
    return f"{secrets.randbelow(9000) + 1000}"

outbreak_engine = OutbreakMonitoringEngine()
