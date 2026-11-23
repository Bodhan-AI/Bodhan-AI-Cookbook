
import json
import time
from pathlib import Path

from bodhan_eval.bodhan import BodhanAPIError, BodhanClient

OUTPUT = (
    Path(__file__).parent
    / "sample_data"
    / "demo_100.jsonl"
)

SENTENCES = [
    "The temperature is 25 degrees today.",
    "The price of this product is 500 rupees.",
    "The meeting starts at 10 AM tomorrow.",
    "We received 100 applications this week.",
    "Please submit the report before Friday.",
    "The train will arrive at platform number 4.",
    "My phone battery is at 15 percent.",
    "The hospital is located 2 kilometers away.",
    "The total payment is 1250 rupees.",
    "The deadline is December 15, 2026.",
    "The server processes 500 requests per second.",
    "Please save the document as a PDF file.",
    "The package contains 12 different items.",
    "Our office opens at 9:30 AM.",
    "The customer ordered 3 laptops.",
    "The internet speed is 100 Mbps.",
    "The medicine should be stored below 25 degrees.",
    "The distance between the cities is 250 kilometers.",
    "The meeting was postponed by two days.",
    "The company hired 45 new employees.",
    "Please turn off the lights before leaving.",
    "The application supports Android and iOS.",
    "The password must contain at least 8 characters.",
    "The project has five major milestones.",
    "The next payment is due on January 5.",
    "The library closes at 6 PM.",
    "The temperature dropped by 7 degrees overnight.",
    "The product warranty lasts for 2 years.",
    "Please contact support at help@example.com.",
    "The total duration of the video is 18 minutes.",
    "The database contains more than 1 million records.",
    "The workshop will begin on Monday at 11 AM.",
    "The discount is valid until March 31.",
    "The file size must not exceed 20 MB.",
    "Please bring your identification card.",
    "The bus leaves every 30 minutes.",
    "The restaurant serves breakfast from 8 AM.",
    "The team completed 8 out of 10 tasks.",
    "The new update fixes 12 security issues.",
    "The user can reset their password using email.",
    "The annual subscription costs 999 rupees.",
    "The city received 120 millimeters of rainfall.",
    "The machine operates at 1500 rotations per minute.",
    "The school has 24 classrooms.",
    "The event will take place in New Delhi.",
    "The flight was delayed by 45 minutes.",
    "The document contains confidential information.",
    "The maximum upload limit is 50 MB.",
    "The employee joined the company in 2022.",
    "Please check the spelling of the customer's name.",
    "The electricity bill increased by 12 percent.",
    "The software requires Python version 3.12.",
    "The package was delivered on November 20.",
    "The recipe requires 250 grams of flour.",
    "The conference has participants from 15 countries.",
    "The train ticket costs 750 rupees.",
    "The system automatically backs up data every day.",
    "The office is closed on public holidays.",
    "The meeting link will be shared through email.",
    "The product weighs approximately 2.5 kilograms.",
    "The candidate scored 82 out of 100 marks.",
    "The application was updated successfully.",
    "Please do not share your OTP with anyone.",
    "The store offers a 10 percent discount.",
    "The delivery address is 24 Park Street.",
    "The customer reported an issue with payment.",
    "The project budget is 5 lakh rupees.",
    "The train journey takes approximately 6 hours.",
    "The device supports Bluetooth version 5.3.",
    "The report was generated automatically.",
    "The meeting room can accommodate 20 people.",
    "The website received 10,000 visitors yesterday.",
    "Please review the changes before merging.",
    "The minimum age requirement is 18 years.",
    "The company was founded in 2015.",
    "The sensor measures humidity and temperature.",
    "The order contains 4 books and 2 notebooks.",
    "The customer paid using UPI.",
    "The application uses PostgreSQL as its database.",
    "The exam will have 50 multiple-choice questions.",
    "The electricity supply was restored at 7 PM.",
    "The package weighs less than 500 grams.",
    "The course includes 12 practical assignments.",
    "The doctor recommended drinking 2 liters of water.",
    "The building has 16 floors.",
    "The payment failed because the card expired.",
    "The user can download the invoice in PDF format.",
    "The conference starts at 9 AM on Tuesday.",
    "The new policy will take effect next month.",
    "The customer requested a refund of 1200 rupees.",
    "The camera records video at 60 frames per second.",
    "The application supports English and 13 Indian languages.",
    "The report must include charts and tables.",
    "The employee worked for 8 hours yesterday.",
    "The total population is approximately 2 million.",
    "The train has 18 coaches.",
    "The backup process takes around 15 minutes.",
    "The customer received a confirmation message.",
    "The server will restart at midnight.",
    "The registration fee is 300 rupees.",
    "The system detected an unusual login attempt.",
    "Please verify the account before making a payment.",
]


LANGUAGES = [
    ("hi", 34),
    ("ta", 33),
    ("bn", 33),
]



def translate_with_retry(client, text, language):
    delays = [30, 60, 120, 180]

    for attempt in range(len(delays) + 1):
        try:
            result = client.translate(text, language)

            if result["truncated"]:
                raise BodhanAPIError("Translation was truncated.")

            return result["translation"].strip()

        except BodhanAPIError as exc:
            if "429" not in str(exc) or attempt == len(delays):
                raise

            wait = delays[attempt]
            print(
                f"Rate limited. Waiting {wait}s "
                f"before retry {attempt + 1}/{len(delays)}..."
            )
            time.sleep(wait)


def main():
    if len(SENTENCES) < 100:
        raise ValueError(f"Expected at least 100 sentences, got {len(SENTENCES)}")

    sentences = SENTENCES[:100]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Resume safely if the script is interrupted.
    existing = {}
    if OUTPUT.exists():
        with OUTPUT.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    record = json.loads(line)
                    existing[record["id"]] = record

    client = BodhanClient()
    sentence_index = 0
    completed = 0

    with OUTPUT.open("a", encoding="utf-8") as file:
        for language, count in LANGUAGES:
            for number in range(1, count + 1):
                record_id = f"{language}-{number:03d}"
                source = sentences[sentence_index]
                sentence_index += 1

                if record_id in existing:
                    completed += 1
                    continue

                print(f"[{completed + 1}/100] Translating {record_id}...")

                translation = translate_with_retry(
                    client,
                    source,
                    language,
                )

                record = {
                    "id": record_id,
                    "language": language,
                    "source_text": source,
                    "translation": translation,
                    "reference_translation": None,
                }

                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                file.flush()

                completed += 1
                print(f"Saved {record_id}")
                time.sleep(2)

    print(f"\nDataset ready: {OUTPUT}")
    print(f"Total records: {completed}")


if __name__ == "__main__":
    main()
