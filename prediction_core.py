"""Pure numerical helpers shared by the live V4 and shadow V4.1 models.

No market, network, or data mutation here; zero is a legitimate statistic.
"""
from __future__ import annotations

import math


def weighted_rate(observations):
    """(value per minute, weight, has_valid_sample) with zero preserved.

    A missing sample is excluded by the caller's valid-sample flag. A real
    observed zero is never silently discarded or replaced with a positive rate.
    """
    valid = []
    for value, weight, available in observations:
        try:
            value, weight = float(value), float(weight)
        except (TypeError, ValueError):
            continue
        if available and math.isfinite(value) and value >= 0 and math.isfinite(weight) and weight > 0:
            valid.append((value, weight))
    total = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / total if total else 0.0


def rate_shadow_v41(season_rate, l10_rate, l5_rate, sample_flags, season_gp):
    """Experimental, deliberately conservative recency alternative.

    This is NOT the live forecast. With fewer than five games, rely chiefly
    on season production; after that, allow recent windows to count more.
    Its quality must be proven with forward-only captures before promotion.
    """
    try:
        gp = max(0.0, float(season_gp))
    except (TypeError, ValueError):
        gp = 0.0
    if gp < 5:
        weights = (0.70, 0.20, 0.10)
    else:
        weights = (0.45, 0.35, 0.20)
    return weighted_rate(zip((season_rate, l10_rate, l5_rate), weights, sample_flags))


def minutes_shadow_v41(season_mean, l10_median, l5_median, sample_flags, injury_status):
    """Candidate minutes model; original V4 minutes are never overwritten."""
    if str(injury_status or "").casefold() in {"out", "doubtful"}:
        return 0.0
    return weighted_rate(zip((season_mean, l10_median, l5_median),
                             (0.25, 0.30, 0.45), sample_flags))


def reliability_label(season_gp, minutes_std, data_valid=True):
    """Descriptive data-quality label, NOT a probability/confidence score."""
    if not data_valid:
        return "Dados insuficientes"
    try:
        gp, spread = float(season_gp), float(minutes_std)
    except (TypeError, ValueError):
        return "Dados insuficientes"
    if not math.isfinite(gp) or gp < 5:
        return "Amostra pequena"
    if not math.isfinite(spread) or spread >= 6:
        return "Minutos instáveis"
    if gp < 10:
        return "Amostra moderada"
    return "Amostra mais estável"
