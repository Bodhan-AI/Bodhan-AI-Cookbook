"""Curated Indic Dataset Downloader for Bodhan Tool Agent.

Fetches authentic, original open-access Indic resources for the 6 Demo Tasks
and unexpected failure test cases from Wikimedia Commons, OpenSLR, arXiv,
and Wikisource.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from src.common.logger import get_logger

logger = get_logger("bodhan.download")

DEFAULT_UA = (
    "BodhanCookbookAgent/1.0 "
    "(https://github.com/7H0M45-4N70NY/Bodhan-AI-Cookbook; thoma@users.noreply.github.com)"
)


@dataclass(frozen=True)
class ResourceSpec:
    key: str
    filename: str
    category: str
    demo_task: str
    description: str
    url: str
    expected_outcome: str  # "pass", "generalize", "fail_refusal", "fail_validation"
    is_synthetic: bool = False
    provenance_type: str = "non-synthetic"  # "non-synthetic" | "synthetic"


# Curated catalog of authentic Indic datasets and edge/failure test cases
CATALOG: dict[str, ResourceSpec] = {
    # Nominal Passing Cases
    "demo1_pdf": ResourceSpec(
        key="demo1_pdf",
        filename="demo1_indic_nlp_paper.pdf",
        category="document",
        demo_task="Demo Task 1: 8-page PDF to 2-min Tamil audio summary",
        description="Authentic research paper on Indic multilingual NLP (AI4Bharat / arXiv:2205.03021)",
        url="https://arxiv.org/pdf/2205.03021.pdf",
        expected_outcome="pass",
    ),
    "demo2_audio": ResourceSpec(
        key="demo2_audio",
        filename="demo2_hindi_spoken_monologue.ogg",
        category="audio",
        demo_task="Demo Task 2: 3-min Hindi audio to English subtitles (SRT)",
        description="Authentic ~3.5 min natural spoken Hindi monologue (Wikitongues / Wikimedia Commons)",
        url="https://upload.wikimedia.org/wikipedia/commons/f/f2/WIKITONGUES_Kavyanjali_speaking_Hindi.ogg",
        expected_outcome="pass",
    ),
    "demo3_image": ResourceSpec(
        key="demo3_image",
        filename="demo3_marathi_devanagari_table.png",
        category="image",
        demo_task="Demo Task 3: Worksheet image to Marathi Markdown preserving tables",
        description="Authentic Devanagari character matrix table (Wikimedia Commons)",
        url="https://upload.wikimedia.org/wikipedia/commons/4/47/UCB_Devanagari.png",
        expected_outcome="pass",
    ),
    "demo4_clip1_te": ResourceSpec(
        key="demo4_clip1_te",
        filename="demo4_telugu_speech_01.webm",
        category="audio",
        demo_task="Demo Task 4: Filter Telugu audio clips & translate",
        description="Authentic spoken Telugu speech clip (Wikitongues / Wikimedia Commons)",
        url="https://upload.wikimedia.org/wikipedia/commons/7/71/WIKITONGUES-_Manjusha_speaking_Telugu.webm",
        expected_outcome="pass",
    ),
    "demo4_clip2_ta": ResourceSpec(
        key="demo4_clip2_ta",
        filename="demo4_tamil_speech_02.webm",
        category="audio",
        demo_task="Demo Task 4: Filter Telugu audio clips & translate",
        description="Authentic spoken Tamil speech clip (Wikitongues / Wikimedia Commons)",
        url="https://upload.wikimedia.org/wikipedia/commons/5/59/WIKITONGUES-_Sai_speaking_Tamil.webm",
        expected_outcome="pass",
    ),
    "demo4_clip3_bn": ResourceSpec(
        key="demo4_clip3_bn",
        filename="demo4_bengali_speech_03.webm",
        category="audio",
        demo_task="Demo Task 4: Filter Telugu audio clips & translate",
        description="Authentic spoken Bengali speech clip (Wikitongues / Wikimedia Commons)",
        url="https://upload.wikimedia.org/wikipedia/commons/a/ab/WIKITONGUES-_Hasive_speaking_Bengali.webm",
        expected_outcome="pass",
    ),
    "demo5_text": ResourceSpec(
        key="demo5_text",
        filename="demo5_bengali_gitanjali_verses.txt",
        category="text",
        demo_task="Demo Task 5: Text file to Bengali female speech (indic-speak)",
        description="Authentic Bengali literature (Rabindranath Tagore's Gitanjali / Wikisource)",
        url="https://bn.wikisource.org/wiki/%E0%A6%97%E0%A7%80%E0%A6%A4%E0%A6%BE%E0%A6%9E%E0%A7%8D%E0%A6%9C%E0%A6%B2%E0%A6%BF",
        expected_outcome="pass",
    ),
    # Failure / Unexpected Boundary Cases
    "fail_refusal_video": ResourceSpec(
        key="fail_refusal_video",
        filename="fail_unsupported_french_video.webm",
        category="video",
        demo_task="Demo Task 6: Video translation to French -> Honest refusal",
        description="Authentic video clip paired with French translation request (triggers honest refusal)",
        url="https://upload.wikimedia.org/wikipedia/commons/8/82/WIKITONGUES-_Krishna_speaking_Hindi.webm",
        expected_outcome="fail_refusal",
    ),
    "fail_corrupt_audio": ResourceSpec(
        key="fail_corrupt_audio",
        filename="fail_corrupted_audio_header.wav",
        category="audio",
        demo_task="Adversarial: Corrupted audio byte stream",
        description="Truncated WAV audio header to test ToolError recovery and graceful agent reaction",
        url="local://fail_corrupted_audio_header.wav",
        expected_outcome="fail_validation",
        is_synthetic=True,
        provenance_type="synthetic",
    ),
    "fail_empty_file": ResourceSpec(
        key="fail_empty_file",
        filename="fail_zero_byte_empty.pdf",
        category="document",
        demo_task="Adversarial: Zero-byte empty PDF",
        description="Empty file to verify input validation defense and circuit breaker",
        url="local://fail_zero_byte_empty.pdf",
        expected_outcome="fail_validation",
        is_synthetic=True,
        provenance_type="synthetic",
    ),
}

# Integrate the Nuanced Edge Cases Suite
try:
    from src.common.edge_cases import EDGE_CASES_CATALOG, generate_all_edge_cases
    CATALOG.update(EDGE_CASES_CATALOG)
except ImportError:
    pass


def download_file(url: str, dest_path: Path, timeout: int = 30) -> Path:
    """Download a file from an authentic public URL using streaming chunks."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("local://"):
        return dest_path
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    logger.info("Downloading %s -> %s", url, dest_path.name)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        with open(dest_path, "wb") as out_file:
            while chunk := response.read(64 * 1024):
                out_file.write(chunk)
    logger.info("Saved %s (%.1f KB)", dest_path.name, dest_path.stat().st_size / 1024)
    return dest_path


def purge_sample_dataset(dest_dir: Path) -> int:
    """Remove downloaded media files from dest_dir, preserving .gitkeep and DATASETS.md."""
    dest_dir = Path(dest_dir)
    if not dest_dir.exists():
        return 0
    count = 0
    for item in dest_dir.iterdir():
        if item.is_file() and item.name not in {".gitkeep", "DATASETS.md"}:
            item.unlink()
            count += 1
        elif item.is_dir() and item.name not in {"__pycache__"}:
            shutil.rmtree(item)
            count += 1
    logger.info("Purged %d downloaded files from %s", count, dest_dir)
    return count


def fetch_sample_dataset(
    dest_dir: Path,
    keys: list[str] | None = None,
    force: bool = False,
    use_network: bool = True,
) -> dict[str, Path]:
    """Fetch all or selected authentic sample datasets into dest_dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Path] = {}
    for key in keys or list(CATALOG.keys()):
        if key not in CATALOG:
            continue
        dest_path = dest_dir / CATALOG[key].filename
        if dest_path.exists() and not force:
            manifest[key] = dest_path
            continue
        # Fallback check for alternate location (root vs edge_cases/ subfolder)
        alt_path = (
            dest_dir / "edge_cases" / CATALOG[key].filename
            if "edge_cases/" not in CATALOG[key].filename
            else dest_dir / Path(CATALOG[key].filename).name
        )
        if alt_path.exists() and not force:
            manifest[key] = alt_path
            continue
        if CATALOG[key].url.startswith("local://"):
            if key == "fail_empty_file" and not dest_path.exists():
                dest_path.write_bytes(b"")
            elif key == "fail_corrupt_audio" and not dest_path.exists():
                dest_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data")
            else:
                try:
                    generate_all_edge_cases(dest_dir)
                except Exception as exc:
                    logger.error("Failed to generate local edge case '%s': %s", key, exc)
            if dest_path.exists():
                manifest[key] = dest_path
            elif alt_path.exists():
                manifest[key] = alt_path
            continue
        if use_network:
            try:
                download_file(CATALOG[key].url, dest_path)
                manifest[key] = dest_path
            except Exception as exc:
                logger.error("Failed to download '%s': %s", key, exc)
    return manifest


def main() -> None:
    """CLI entrypoint for dataset acquisition, generation, and cleanup."""
    parser = argparse.ArgumentParser(description="Fetch or clean Indic sample datasets.")
    parser.add_argument("--dest-dir", type=Path, default=Path("sample_data"), help="Target folder")
    parser.add_argument("--force", action="store_true", help="Force redownload existing files")
    parser.add_argument("--clean", action="store_true", help="Purge downloaded files from sample_data (reset to clean slate)")
    parser.add_argument("--keys", nargs="+", help="Specific dataset keys to fetch")
    args = parser.parse_args()

    if args.clean:
        purged = purge_sample_dataset(args.dest_dir)
        print(f"Purged {purged} downloaded sample file(s) from {args.dest_dir}. Clean slate restored.")
        return

    manifest = fetch_sample_dataset(args.dest_dir, keys=args.keys, force=args.force)
    print(f"Dataset status in {args.dest_dir}: {len(manifest)}/{len(args.keys or CATALOG)} available")
    for k, p in manifest.items():
        sz = p.stat().st_size / 1024 if p.exists() else 0
        print(f"  [{k}] -> {p.name} ({sz:.1f} KB, expected: {CATALOG[k].expected_outcome})")

    if args.keys is None:
        from src.common.random_sampler import generate_random_multimodal_sample

        random_manifest = generate_random_multimodal_sample(
            args.dest_dir,
            preserve_existing=not args.force,
        )
        total = sum(len(paths) for paths in random_manifest.values())
        print(
            f"Random synthetic samples in {args.dest_dir}: {total} "
            f"(speech: {len(random_manifest['speech'])}, "
            f"images: {len(random_manifest['images'])}, "
            f"documents: {len(random_manifest['documents'])}, "
            f"text: {len(random_manifest['text'])})"
        )


if __name__ == "__main__":
    main()
