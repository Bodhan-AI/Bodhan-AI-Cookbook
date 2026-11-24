"""Provenance of produced files and the verification each deliverable needs.

Every file a tool writes is recorded with the call that produced it. When the
planner proposes deliverables, the gate works out what must have passed:
- audio from synthesize_speech needs a passing back-transcription check
  (synthesize_speech's own `verify`, or verify_speech);
- a translation from translate_text needs verify_translation;
- subtitles built from translated segments inherit that translation's check.
Checks that were never run are run automatically before the run can finish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from bodhan_agent.guards import canonical

Kind = Literal["audio", "translation", "subtitle", "text", "transcript", "ocr", "other"]

READ_ONLY_TOOLS = frozenset({"inspect_file", "read_text", "list_languages_and_voices"})
VERIFY_TOOLS = frozenset({"verify_speech", "verify_translation"})
_KIND_BY_TOOL: dict[str, Kind] = {
    "synthesize_speech": "audio",
    "translate_text": "translation",
    "make_subtitles": "subtitle",
    "transcribe_audio": "transcript",
    "ocr_document": "ocr",
    "write_text_artifact": "text",
}


@dataclass
class Provenance:
    path: str
    tool: str
    kind: Kind
    language: str | None = None
    source_path: str | None = None
    source_text: str | None = None
    parent: str | None = None
    # translate_text writes a .txt and (for segment input) a .json; both share one check.
    check_key: str | None = None


@dataclass
class VerificationRecord:
    key: str
    kind: Kind
    passed: bool
    by: str
    issues: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.key, "kind": self.kind, "passed": self.passed, "by": self.by,
                "issues": self.issues, "details": self.details}


@dataclass
class Requirement:
    artifact: str
    key: str
    kind: Kind
    tool: str
    arguments: dict[str, Any]


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def output_paths(data: Any, key: str = "") -> list[tuple[str, str]]:
    """(key, path) pairs for every `*_path` string in a tool result."""
    found: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            k = str(k)
            if (k.endswith("_path") or k == "path") and _str(v):
                found.append((k, v))
            elif isinstance(v, (dict, list)):
                found.extend(output_paths(v, k))
    elif isinstance(data, list):
        for v in data:
            found.extend(output_paths(v, key))
    return found


AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac"})


def _kind_of(tool: str, out_key: str, path: str) -> Kind:
    if tool in VERIFY_TOOLS:
        return "other"
    kind = _KIND_BY_TOOL.get(tool, "other")
    # Side files (e.g. back-transcripts written during synthesis checks) are not the deliverable itself.
    if kind == "audio" and Path(path).suffix.lower() not in AUDIO_SUFFIXES:
        return "other"
    if kind == "translation" and out_key not in ("output_path", "segments_path"):
        return "other"
    return kind


def _issues(data: dict[str, Any]) -> list[str]:
    raw = data.get("issues") or data.get("failed_chunks") or []
    if isinstance(raw, list):
        return [str(x) if not isinstance(x, dict) else ", ".join(f"{k}={v}" for k, v in x.items()) for x in raw]
    return [str(raw)]


class Ledger:
    def __init__(self) -> None:
        self.provenance: dict[str, Provenance] = {}
        self.records: dict[str, VerificationRecord] = {}
        self.history: list[VerificationRecord] = []

    def get(self, path: str) -> Provenance | None:
        return self.provenance.get(canonical(path))

    def produced(self) -> list[Provenance]:
        return list(self.provenance.values())

    def record_text_file(self, path: str) -> None:
        key = canonical(path)
        self.provenance[key] = Provenance(path=path, tool="write_text_artifact", kind="text", language="en")
        # A rewritten file is a different text; any old check no longer applies.
        self.records.pop(key, None)

    def record_tool(self, name: str, args: dict[str, Any], data: dict[str, Any]) -> list[Provenance]:
        """Register outputs of a successful call and any verification it carried."""
        if name in READ_ONLY_TOOLS:
            return []
        if name == "verify_speech":
            self._record_speech_check(args, data)
        elif name == "verify_translation":
            self._record_translation_check(args, data)
        arg_paths = {canonical(p) for p in (args.get("path"), args.get("segments_path"),
                                            args.get("audio_path"), args.get("translation_path")) if _str(p)}
        new: list[Provenance] = []
        primary_translation: str | None = None
        if name == "translate_text":
            primary_translation = _str(data.get("output_path")) or _str(data.get("segments_path"))
        for out_key, path in output_paths(data):
            ckey = canonical(path)
            if ckey in arg_paths:
                continue
            kind = _kind_of(name, out_key, path)
            prov = Provenance(
                path=path,
                tool=name,
                kind=kind,
                language=(_str(args.get("target_language")) or _str(data.get("language"))
                          or _str(args.get("language"))),
                source_path=_str(args.get("path")),
                source_text=_str(args.get("text")),
                parent=_str(args.get("segments_path")),
                check_key=canonical(primary_translation) if primary_translation else ckey,
            )
            self.provenance[ckey] = prov
            self.records.pop(ckey, None)
            new.append(prov)
        if name == "synthesize_speech":
            self._record_synthesis_check(args, data)
        return new

    def _store(self, record: VerificationRecord) -> None:
        self.records[record.key] = record
        self.history.append(record)

    def _record_synthesis_check(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        audio = _str(data.get("audio_path"))
        ver = data.get("verification")
        if not audio or not isinstance(ver, dict) or ver.get("passed") is None or args.get("verify") is False:
            return
        self._store(VerificationRecord(canonical(audio), "audio", bool(ver.get("passed")), "synthesize_speech",
                                       _issues(ver), {k: ver.get(k) for k in ("mean_cer",) if k in ver}))

    def _record_speech_check(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        audio = _str(args.get("audio_path"))
        if not audio or data.get("passed") is None:
            return
        details = {k: data.get(k) for k in ("cer", "length_ratio", "repetition_detected", "omission_suspected")
                   if k in data}
        self._store(VerificationRecord(canonical(audio), "audio", bool(data["passed"]), "verify_speech",
                                       _issues(data), details))

    def _record_translation_check(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        target = _str(args.get("translation_path"))
        if not target or data.get("passed") is None:
            return
        prov = self.get(target)
        key = prov.check_key if prov and prov.check_key else canonical(target)
        self._store(VerificationRecord(key, "translation", bool(data["passed"]), "verify_translation",
                                       _issues(data)))

    def requirement_for(self, artifact: str) -> Requirement | None:
        prov = self.get(artifact)
        if prov is None:
            return None
        if prov.kind == "subtitle" and prov.parent:
            parent = self.get(prov.parent)
            if parent is None or parent.kind != "translation":
                return None
            prov = parent
        if prov.kind == "audio":
            args: dict[str, Any] = {"audio_path": prov.path, "language": prov.language or "auto"}
            if prov.source_path:
                args["expected_path"] = prov.source_path
            elif prov.source_text:
                args["expected_text"] = prov.source_text
            return Requirement(artifact, canonical(prov.path), "audio", "verify_speech", args)
        if prov.kind == "translation":
            key = prov.check_key or canonical(prov.path)
            check_target = next((p.path for p in self.provenance.values() if canonical(p.path) == key), prov.path)
            args = {"translation_path": check_target, "target_language": prov.language or ""}
            if prov.source_path:
                args["source_path"] = prov.source_path
            elif prov.source_text:
                args["source_text"] = prov.source_text
            return Requirement(artifact, key, "translation", "verify_translation", args)
        return None

    def status(self, artifact: str) -> VerificationRecord | None:
        req = self.requirement_for(artifact)
        return self.records.get(req.key) if req else None

    def deliverable_candidates(self) -> list[Provenance]:
        """Files worth handing over when the run stops without an explicit artifact list."""
        skip_tools = VERIFY_TOOLS
        seen: set[str] = set()
        out: list[Provenance] = []
        for prov in self.provenance.values():
            if prov.tool in skip_tools or not Path(prov.path).exists():
                continue
            key = canonical(prov.path)
            if key not in seen:
                seen.add(key)
                out.append(prov)
        return out
