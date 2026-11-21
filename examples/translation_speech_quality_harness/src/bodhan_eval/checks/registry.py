from dataclasses import dataclass
from typing import Any, Callable

from ..config import CHECK_THRESHOLDS
from ..models import Flag, Item


@dataclass(frozen=True)
class LangStats:
    median_length_ratio: float = 1.0
    mad_length_ratio: float = CHECK_THRESHOLDS["text_length_default_mad"]["value"]
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class CheckSpec:
    name: str
    categories: tuple[str, ...]
    modality: str
    cost_tier: int
    runner: Callable[[Item, LangStats], list[Flag]]


CHECK_REGISTRY: dict[str, CheckSpec] = {}


def register_check(
    *,
    name: str,
    categories: list[str],
    modality: str,
    cost_tier: int,
):
    if not name or not categories:
        raise ValueError("check name and categories are required")
    if modality not in {"text", "speech"}:
        raise ValueError("check modality must be text or speech")
    if cost_tier not in {0, 1, 2, 3}:
        raise ValueError("check cost_tier must be 0, 1, 2, or 3")

    def decorator(runner: Callable[[Item, LangStats], list[Flag]]):
        if name in CHECK_REGISTRY:
            raise ValueError(f"check already registered: {name}")
        CHECK_REGISTRY[name] = CheckSpec(
            name=name,
            categories=tuple(sorted(set(categories))),
            modality=modality,
            cost_tier=cost_tier,
            runner=runner,
        )
        return runner

    return decorator


def registered_checks(modality: str | None = None) -> list[CheckSpec]:
    specs = [
        spec for spec in CHECK_REGISTRY.values()
        if modality is None or spec.modality == modality
    ]
    return sorted(specs, key=lambda spec: (spec.cost_tier, spec.name))
