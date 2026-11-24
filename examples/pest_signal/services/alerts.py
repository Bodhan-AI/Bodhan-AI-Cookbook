from database import get_reports, upsert_alert

THRESHOLD = 3


def normalized(s):
    return " ".join((s or "").lower().replace(",", " ").replace(".", " ").replace("-", " ").split())


def evaluate_report(report):
    region = normalized(report.get("location"))

    if not region:
        print("[ALERT] No region found")
        return

    rows = get_reports()

    matched = [r["id"] for r in rows if normalized(r.get("location")) == region]

    print("Alert Debug:")
    print("Submitted region:", repr(region))
    print("Total reports:", len(rows))
    print("Matching IDs:", matched)
    print("Matching count:", len(matched))
    print("Threshold:", THRESHOLD)

    if len(matched) >= THRESHOLD:
        print("THRESHOLD PASSED")

        upsert_alert(report["location"], "Multiple crops", "Regional agricultural signal", matched)

        print("UPSERT EXECUTED")

    else:
        print("THRESHOLD NOT PASSED")
