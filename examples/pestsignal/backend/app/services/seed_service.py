import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.report import Report
from app.models.alert import Alert
from app.models.note import InvestigationNote
from app.models.notification import Notification
from app.core.security import hash_password
from app.services.outbreak_service import outbreak_engine

logger = logging.getLogger(__name__)

# Sample Indian coordinates for districts
DISTRICT_COORDS = {
    "Guntur": (16.3067, 80.4365),
    "Karimnagar": (18.4386, 79.1288),
    "Ludhiana": (30.9010, 75.8573),
    "Khammam": (17.2473, 80.1514),
    "Kolar": (13.1367, 78.1340),
    "Nashik": (19.9975, 73.7898),
    "Varanasi": (25.3176, 82.9739),
}

def seed_database(db: Session):
    """Seed the database with default accounts and demonstration reports."""
    # Check if already seeded
    existing_user = db.query(User).first()
    if existing_user:
        return

    logger.info("Initializing database with initial demo data...")

    # 1. Create Users
    farmer = User(
        full_name="Ramu Chowdary",
        email="farmer@pestsignal.org",
        phone="+91 98480 22334",
        password_hash=hash_password("farmer123"),
        role="farmer",
        state="Andhra Pradesh",
        district="Guntur",
        preferred_language="te",
        created_at=datetime.utcnow() - timedelta(days=20)
    )

    farmer2 = User(
        full_name="Rajesh Patel",
        email="rajesh@pestsignal.org",
        phone="+91 98250 11223",
        password_hash=hash_password("farmer123"),
        role="farmer",
        state="Gujarat",
        district="Rajkot",
        preferred_language="gu",
        created_at=datetime.utcnow() - timedelta(days=15)
    )

    officer = User(
        full_name="Dr. Anita Sharma",
        email="officer@pestsignal.org",
        phone="+91 94401 55667",
        password_hash=hash_password("officer123"),
        role="officer",
        state="Andhra Pradesh",
        district="Guntur",
        preferred_language="en",
        created_at=datetime.utcnow() - timedelta(days=30)
    )

    admin = User(
        full_name="Platform Administrator",
        email="admin@pestsignal.org",
        phone="+91 98110 99887",
        password_hash=hash_password("admin123"),
        role="admin",
        state="Delhi",
        district="Central Delhi",
        preferred_language="en",
        created_at=datetime.utcnow() - timedelta(days=60)
    )

    db.add_all([farmer, farmer2, officer, admin])
    db.flush()

    # 2. Create Reports
    reports_data = [
        # Cluster 1: Cotton in Guntur, Andhra Pradesh (4 reports => triggers alert)
        {
            "id": "PS-2026-1001",
            "farmer_id": farmer.id,
            "crop_name": "Cotton",
            "crop_variety": "Bt Cotton RCH-2",
            "growth_stage": "Flowering stage",
            "symptoms": "Brown spots, Yellow leaves, Leaf curling",
            "source_language": "te",
            "original_transcription": "నా పత్తి పంటలో ఆకులపై బ్రౌన్ మచ్చలు వస్తున్నాయి మరియు ఆకులు ఎండిపోయి రాలిపోతున్నాయి. పూత కూడా రాలిపోతుంది.",
            "english_translation": "In my cotton crop, brown spots are appearing on the leaves and leaves are drying and shedding. Flowers are also falling.",
            "submission_method": "voice",
            "state": "Andhra Pradesh",
            "district": "Guntur",
            "village": "Prathipadu",
            "farm_location": "Survey No. 42/B, North Block",
            "latitude": 16.3120,
            "longitude": 80.4410,
            "observation_date": (datetime.utcnow() - timedelta(days=4)).strftime("%Y-%m-%d"),
            "affected_plants": 150,
            "investigation_status": "Investigation",
            "created_at": datetime.utcnow() - timedelta(days=4)
        },
        {
            "id": "PS-2026-1002",
            "farmer_id": farmer.id,
            "crop_name": "Cotton",
            "crop_variety": "Hybrid Cotton",
            "growth_stage": "Boll formation",
            "symptoms": "Brown spots, Holes in leaves",
            "source_language": "te",
            "original_transcription": "పత్తి కాయలపై మరియు ఆకులపై తీవ్రమైన నల్ల మచ్చలు కనిపిస్తున్నాయి. కాయలు కుళ్ళిపోతున్నాయి.",
            "english_translation": "Severe black spots are visible on cotton bolls and leaves. Bolls are rotting rapidly.",
            "submission_method": "voice",
            "state": "Andhra Pradesh",
            "district": "Guntur",
            "village": "Medikonduru",
            "farm_location": "East Canal side",
            "latitude": 16.2950,
            "longitude": 80.4220,
            "observation_date": (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d"),
            "affected_plants": 200,
            "investigation_status": "Under Review",
            "created_at": datetime.utcnow() - timedelta(days=3)
        },
        {
            "id": "PS-2026-1003",
            "farmer_id": farmer.id,
            "crop_name": "Cotton",
            "crop_variety": "Bt Cotton",
            "growth_stage": "Vegetative stage",
            "symptoms": "Brown spots, Yellow leaves",
            "source_language": "te",
            "original_transcription": "చెట్ల దిగువ ఆకులపై గుండ్రని ముదురు గోధుమ రంగు మచ్చలు ఉన్నాయి. ఎరువులు వేసినా మార్పు లేదు.",
            "english_translation": "There are circular dark brown spots on lower leaves of the plants. No change even after applying fertilizer.",
            "submission_method": "text",
            "state": "Andhra Pradesh",
            "district": "Guntur",
            "village": "Tadikonda",
            "farm_location": "Plot 15",
            "latitude": 16.4200,
            "longitude": 80.4600,
            "observation_date": (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d"),
            "affected_plants": 90,
            "investigation_status": "Submitted",
            "created_at": datetime.utcnow() - timedelta(days=2)
        },
        {
            "id": "PS-2026-1004",
            "farmer_id": farmer.id,
            "crop_name": "Cotton",
            "crop_variety": "Desi Cotton",
            "growth_stage": "Flowering stage",
            "symptoms": "Brown spots, Leaf curling",
            "source_language": "te",
            "original_transcription": "ఆకులు పైకి ముడుచుకుని పోతున్నాయి మరియు అంచులలో ఎర్రటి మచ్చలు వస్తున్నాయి.",
            "english_translation": "Leaves are curling upwards and reddish spots are developing along the margins.",
            "submission_method": "voice",
            "state": "Andhra Pradesh",
            "district": "Guntur",
            "village": "Prathipadu",
            "farm_location": "Field 8",
            "latitude": 16.3010,
            "longitude": 80.4300,
            "observation_date": (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d"),
            "affected_plants": 120,
            "investigation_status": "Submitted",
            "created_at": datetime.utcnow() - timedelta(days=1)
        },

        # Cluster 2: Paddy / Rice in Karimnagar, Telangana (3 reports)
        {
            "id": "PS-2026-1005",
            "farmer_id": farmer.id,
            "crop_name": "Rice (Paddy)",
            "crop_variety": "BPT 5204 (Samba Masuri)",
            "growth_stage": "Tillering stage",
            "symptoms": "Yellow leaves, Stem damage, Wilting",
            "source_language": "te",
            "original_transcription": "వరి పైరులో ఆకులు పసుపు రంగులోకి మారి ఎండిపోతున్నాయి. కాండం లోపల పురుగులు తింటున్నాయి.",
            "english_translation": "In paddy crop, leaves are turning yellow and drying up. Insects are feeding inside the stem.",
            "submission_method": "voice",
            "state": "Telangana",
            "district": "Karimnagar",
            "village": "Choppadandi",
            "farm_location": "Main canal road",
            "latitude": 18.4450,
            "longitude": 79.1350,
            "observation_date": (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d"),
            "affected_plants": 350,
            "investigation_status": "Under Review",
            "created_at": datetime.utcnow() - timedelta(days=5)
        },
        {
            "id": "PS-2026-1006",
            "farmer_id": farmer.id,
            "crop_name": "Rice (Paddy)",
            "crop_variety": "Telangana Sona",
            "growth_stage": "Panicle initiation",
            "symptoms": "Yellow leaves, Stem damage",
            "source_language": "te",
            "original_transcription": "వరి దుబ్బులు పచ్చదనం కోల్పోయి చనిపోతున్నాయి. మొలక కేంద్రంలో తెల్లటి కంకులు కనిపిస్తున్నాయి.",
            "english_translation": "Paddy clumps are losing greenery and dying. White ears are visible at the shoot center.",
            "submission_method": "voice",
            "state": "Telangana",
            "district": "Karimnagar",
            "village": "Manakondur",
            "farm_location": "Near Panchayat office",
            "latitude": 18.4200,
            "longitude": 79.1100,
            "observation_date": (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d"),
            "affected_plants": 280,
            "investigation_status": "Investigation",
            "created_at": datetime.utcnow() - timedelta(days=3)
        },
        {
            "id": "PS-2026-1007",
            "farmer_id": farmer.id,
            "crop_name": "Rice (Paddy)",
            "crop_variety": "MTU 1010",
            "growth_stage": "Tillering stage",
            "symptoms": "Yellow leaves, Insect infestation",
            "source_language": "te",
            "original_transcription": "పొలమంతా ఆకు చివరలు పసుపుగా మారుతున్నాయి మరియు నీటి మట్టం వద్ద చిన్న పురుగులు ఉన్నాయి.",
            "english_translation": "Across the field, leaf tips are yellowing and small insects are observed near the water level.",
            "submission_method": "text",
            "state": "Telangana",
            "district": "Karimnagar",
            "village": "Kothapally",
            "farm_location": "Survey 102",
            "latitude": 18.4500,
            "longitude": 79.1400,
            "observation_date": (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d"),
            "affected_plants": 180,
            "investigation_status": "Submitted",
            "created_at": datetime.utcnow() - timedelta(days=2)
        },

        # Other regional reports
        {
            "id": "PS-2026-1008",
            "farmer_id": farmer2.id,
            "crop_name": "Chilli",
            "crop_variety": "Teja Chilli",
            "growth_stage": "Fruiting stage",
            "symptoms": "Leaf curling, Wilting, Insect infestation",
            "source_language": "hi",
            "original_transcription": "मिर्च के पौधों की पत्तियां ऊपर की तरफ मुड़ रही हैं और फूल झड़ रहे हैं। सफेद मक्खी का प्रकोप ज्यादा है।",
            "english_translation": "Chilli plant leaves are curling upwards and flowers are shedding. Whitefly infestation is severe.",
            "submission_method": "voice",
            "state": "Telangana",
            "district": "Khammam",
            "village": "Wyra",
            "farm_location": "Near lake",
            "latitude": 17.2473,
            "longitude": 80.1514,
            "observation_date": (datetime.utcnow() - timedelta(days=6)).strftime("%Y-%m-%d"),
            "affected_plants": 400,
            "investigation_status": "Resolved",
            "created_at": datetime.utcnow() - timedelta(days=6)
        },
        {
            "id": "PS-2026-1009",
            "farmer_id": farmer2.id,
            "crop_name": "Wheat",
            "crop_variety": "HD-3086",
            "growth_stage": "Heading stage",
            "symptoms": "Yellow leaves, Holes in leaves",
            "source_language": "hi",
            "original_transcription": "गेहूं की पत्तियों पर पीली धारियां दिखाई दे रही हैं जो छूने पर हाथ पर पीला पाउडर छोड़ती हैं।",
            "english_translation": "Yellow stripes are appearing on wheat leaves that leave yellow powder on hand when touched.",
            "submission_method": "voice",
            "state": "Punjab",
            "district": "Ludhiana",
            "village": "Jagraon",
            "farm_location": "GT Road side",
            "latitude": 30.9010,
            "longitude": 75.8573,
            "observation_date": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d"),
            "affected_plants": 250,
            "investigation_status": "Resolved",
            "created_at": datetime.utcnow() - timedelta(days=7)
        }
    ]

    for r_data in reports_data:
        rep = Report(**r_data)
        db.add(rep)

    db.flush()

    # 3. Add Investigation Notes
    note1 = InvestigationNote(
        report_id="PS-2026-1001",
        officer_id=officer.id,
        officer_name=officer.full_name,
        note="Field team dispatched to Prathipadu. Suspecting Cercospora / Alternaria leaf spot complex aggravated by recent rains. Advised farmer to avoid excessive nitrogenous top-dressing.",
        created_at=datetime.utcnow() - timedelta(days=3)
    )
    note2 = InvestigationNote(
        report_id="PS-2026-1006",
        officer_id=officer.id,
        officer_name=officer.full_name,
        note="Symptoms strongly resemble Yellow Stem Borer (Scirpophaga incertulas). Pheromone traps recommended at 8 traps/acre.",
        created_at=datetime.utcnow() - timedelta(days=2)
    )
    db.add_all([note1, note2])

    # 4. Add Initial Farmer Notifications
    notif1 = Notification(
        user_id=farmer.id,
        title="Report Under Review",
        message="Your crop report PS-2026-1001 for Cotton in Guntur has been assigned to Dr. Anita Sharma for investigation.",
        type="report",
        link="/farmer/reports/PS-2026-1001",
        is_read=False,
        created_at=datetime.utcnow() - timedelta(days=3)
    )
    notif2 = Notification(
        user_id=farmer.id,
        title="Report Submitted Successfully",
        message="Your report PS-2026-1004 has been logged into the central PestSignal agricultural database.",
        type="report",
        link="/farmer/reports/PS-2026-1004",
        is_read=True,
        created_at=datetime.utcnow() - timedelta(days=1)
    )
    db.add_all([notif1, notif2])

    db.commit()

    # 5. Run Outbreak Engine to auto-generate initial alerts based on the real seeded data!
    outbreak_engine.run_detection(db)
    logger.info("Database seeding successfully completed!")
