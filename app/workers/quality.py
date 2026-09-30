from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class ParseQuality:
    accepted: bool
    score: float
    reason: str | None
    character_count: int
    alnum_ratio: float
    replacement_ratio: float


def evaluate_text_quality(text: str | None, page_count: int = 1) -> ParseQuality:
    value = (text or "").strip()
    count = len(value)
    denominator = max(count, 1)
    alnum_ratio = sum(character.isalnum() for character in value) / denominator
    replacement_ratio = value.count("\ufffd") / denominator
    minimum = max(
        settings.PARSE_MIN_TEXT_CHARACTERS,
        max(page_count, 1) * settings.PARSE_MIN_TEXT_CHARACTERS_PER_PAGE,
    )

    reason = None
    if count < minimum:
        reason = "text_too_short"
    elif replacement_ratio > settings.PARSE_MAX_REPLACEMENT_RATIO:
        reason = "too_many_replacement_characters"
    elif alnum_ratio < settings.PARSE_MIN_ALNUM_RATIO:
        reason = "text_signal_too_low"

    length_score = min(1.0, count / max(minimum, 1))
    signal_score = min(1.0, alnum_ratio / max(settings.PARSE_MIN_ALNUM_RATIO, 0.01))
    if settings.PARSE_MAX_REPLACEMENT_RATIO > 0:
        replacement_score = max(
            0.0,
            1.0 - replacement_ratio / settings.PARSE_MAX_REPLACEMENT_RATIO,
        )
    else:
        replacement_score = 1.0 if replacement_ratio == 0 else 0.0
    score = round((length_score * 0.4) + (signal_score * 0.4) + (replacement_score * 0.2), 4)

    return ParseQuality(
        accepted=reason is None,
        score=score,
        reason=reason,
        character_count=count,
        alnum_ratio=round(alnum_ratio, 4),
        replacement_ratio=round(replacement_ratio, 4),
    )
