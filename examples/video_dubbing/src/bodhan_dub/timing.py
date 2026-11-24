"""Duration-drift policy: a ladder of fixes tried in order, so speed-ups stay natural.

1. fit         dubbed line fits its slot
2. borrow_gap  it runs into the silence after the slot (start is unchanged)
3. lead        it also starts a little early (within the sync tolerance)
4. stretch     mild time-stretch, never above 1.2x
5. condense    still too long: ask the LLM for a shorter English line and re-translate
"""
from dataclasses import asdict, dataclass

from . import config


@dataclass
class FitPlan:
    action: str          # fit | borrow_gap | lead | stretch | condense
    stretch: float       # speed factor, >1 = faster
    start_shift: float   # seconds added to the slot start (<=0 means earlier)
    overrun: float       # seconds still over budget after this action

    def as_dict(self):
        return asdict(self)


def plan_fit(d: float, slot: float, gap_after: float, gap_before: float,
             max_stretch: float = config.STRETCH_MAX, max_lead: float = 0.25, tol: float = 0.02) -> FitPlan:
    if d <= slot + tol:
        return FitPlan("fit", 1.0, 0.0, 0.0)
    if d <= slot + gap_after + tol:
        return FitPlan("borrow_gap", 1.0, 0.0, 0.0)
    lead = min(max_lead, gap_before)
    avail = slot + gap_after + lead
    if d <= avail + tol:
        return FitPlan("lead", 1.0, -(d - slot - gap_after), 0.0)
    f = d / avail
    if f <= max_stretch:
        return FitPlan("stretch", f, -lead, 0.0)
    return FitPlan("condense", max_stretch, -lead, d / max_stretch - avail)


def condense_ratio(d: float, slot: float, gap_after: float, gap_before: float, headroom: float = 1.1) -> float:
    """Fraction of the current text length that should fit with at most `headroom` speed-up."""
    avail = slot + gap_after + min(0.25, gap_before)
    return max(0.3, min(0.95, avail * headroom / d))


def fmt_srt(t: float) -> str:
    t = max(0.0, t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def make_srt(items: list[tuple[float, float, str]]) -> str:
    blocks = [f"{i}\n{fmt_srt(a)} --> {fmt_srt(b)}\n{t}\n" for i, (a, b, t) in enumerate(items, 1)]
    return "\n".join(blocks)
