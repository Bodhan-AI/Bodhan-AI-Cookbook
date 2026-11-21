"""Prepare labeled synthetic speech fixtures through real Bodhan tools only.

The model-facing mixed folder contains numbered WAVs only. Language labels and
generation/verification evidence stay in the separate preparation report.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from dotenv import load_dotenv

from bodhan_core.gateway import Gateway
from bodhan_core.media import command
from bodhan_core.runtime import Limits, Run, ToolError, decimal
from bodhan_core.tools import Tools


async def prepare(inputs, out, cap, bengali_report):
    run = Run(inputs / "audio_sources", out, Limits(max_cost=decimal(cap), max_steps=20))
    gateway = Gateway(run)
    tools = Tools(run, gateway)
    mixed, note = inputs / "mixed_clips", inputs / "hindi_note"
    mixed.mkdir(exist_ok=True)
    note.mkdir(exist_ok=True)
    evidence, selected = [], []
    try:
        for source_name, language, number in [
            ("telugu_one", "te", 1),
            ("tamil", "ta", 2),
            ("hindi", "hi", 3),
            ("telugu_two", "te", 4),
        ]:
            source = run.register(run.input_root / (source_name + ".txt"), "input")
            translated = await tools.call(
                "translate", {"source": source, "target_language": language}
            )
            evidence.append({"language": language, "translation": translated})
            if not translated.get("ok") or not translated["verification"]["passed"]:
                raise ToolError(
                    "fixture_translation", "Fixture translation failed; no further requests."
                )
            speech = await tools.call(
                "synthesize", {"source": translated["ref"], "language": language}
            )
            evidence[-1]["speech"] = speech
            if not speech.get("ok") or not speech["verification"]["passed"]:
                raise ToolError(
                    "fixture_speech", "Fixture speech failed verification; no further requests."
                )
            target = mixed / f"clip_{number}.wav"
            shutil.copyfile(run.resolve(speech["ref"]), target)
            evidence[-1].update(
                fixture=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest()
            )
            selected.append(speech["ref"])
            if language == "hi":
                command(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(target),
                        "-t",
                        "180",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(note / "synthetic_loop_180s.wav"),
                    ],
                    run.remaining_time(),
                )
        prior = json.loads(bengali_report.read_text())
        candidates = [a for a in prior["artifacts"] if a["selected"] and a["path"].endswith(".wav")]
        if not candidates or not any(
            c.get("ref") == candidates[0]["ref"]
            and c.get("checked") is True
            and c.get("passed") is True
            for c in prior["verification"]
        ):
            raise ToolError("fixture_source", "Bengali source has no selected verified WAV.")
        source = (bengali_report.parent / candidates[0]["path"]).resolve(strict=True)
        if not source.is_relative_to(bengali_report.parent.resolve()):
            raise ToolError("fixture_source", "Bengali source escaped its report directory.")
        target = mixed / "clip_5.wav"
        shutil.copyfile(source, target)
        evidence.append(
            {
                "language": "bn",
                "fixture": str(target),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source_report": str(bengali_report),
                "reused_verified_audio": True,
            }
        )
        return run.write_report(
            "completed",
            "Four Bodhan synthetic speech fixtures prepared and verified; one existing verified Bengali fixture reused. Hindi is an artificial 180-second loop, not natural speech.",
            selected,
        )
    except ToolError as exc:
        return run.write_report("partial", f"{exc.code}: {exc}", selected)
    finally:
        (out / "fixture_evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2)
        )
        await gateway.close()


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-cost-inr", required=True)
    parser.add_argument("--bengali-report", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(prepare(args.inputs, args.out, args.max_cost_inr, args.bengali_report))
    print(
        json.dumps(
            {k: report[k] for k in ["status", "total_cost_inr", "total_seconds", "explanation"]}
        )
    )
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
