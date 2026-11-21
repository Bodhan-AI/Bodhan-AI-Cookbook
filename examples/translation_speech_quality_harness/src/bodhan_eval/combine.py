from .models import Flag


def combine_flags(flag_groups: list[list[Flag]]) -> list[Flag]:
    flags: dict[tuple[str, str, str], Flag] = {}
    for group in flag_groups:
        for flag in group:
            key = (flag.category, flag.modality, str(flag.span))
            existing = flags.get(key)
            if existing is None or flag.confidence > existing.confidence:
                flags[key] = flag
    return sorted(flags.values(), key=lambda flag: (
        flag.category,
        flag.modality,
        str(flag.span),
        flag.severity,
    ))
