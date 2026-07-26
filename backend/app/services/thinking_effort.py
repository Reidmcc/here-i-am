"""
Thinking Effort

One canonical scale for "how hard should the model think", translated per
provider at the call site:

- Anthropic: `output_config.effort` (low | medium | high | xhigh | max)
- OpenAI:    `reasoning_effort` (low | medium | high) on reasoning models
- Google:    `thinking_config` (thinking_level on Gemini 3, thinking_budget on 2.5)

The canonical levels below are Anthropic's, because they are the finest-grained
of the three; the other providers collapse them (see each provider service).
Models that don't take an effort/thinking parameter simply ignore the setting —
nothing extra is sent for them.

Levels are per entity (`EntitySetting.thinking_effort`), falling back to
`settings.default_thinking_effort` ("high") when an entity has none set.
"""

from typing import Optional, Sequence

from app.config import settings

# Ordered lowest → highest. This ordering is load-bearing: clamping a requested
# level to a model's ceiling picks the highest supported level at or below it.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

_EFFORT_RANK = {level: index for index, level in enumerate(EFFORT_LEVELS)}


def is_valid_effort(value: Optional[str]) -> bool:
    """Whether the value is one of the canonical effort levels."""
    return isinstance(value, str) and value.lower() in _EFFORT_RANK


def normalize_effort(value: Optional[str]) -> Optional[str]:
    """
    Normalize a caller-supplied effort level, or None if it isn't a valid one.

    Empty/whitespace strings normalize to None ("no explicit setting"), which
    callers resolve to the configured default.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not candidate:
        return None
    return candidate if candidate in _EFFORT_RANK else None


def resolve_effort(value: Optional[str]) -> str:
    """
    Resolve an effort level to send, falling back to the configured default.

    An unrecognized value falls back rather than raising: the setting is a
    tuning knob, and a bad value should not take a turn down with it.
    """
    return normalize_effort(value) or normalize_effort(settings.default_thinking_effort) or "high"


def clamp_effort(effort: str, supported: Sequence[str]) -> Optional[str]:
    """
    Clamp an effort level to the highest supported level at or below it.

    Older models support a shorter ladder (no `xhigh`, no `max`); sending a
    level they don't know is a 400, so requests above a model's ceiling are
    lowered instead of dropped. Returns None when the model supports no
    levels at all (i.e. takes no effort parameter).
    """
    if not supported:
        return None
    requested_rank = _EFFORT_RANK[effort]
    candidates = [
        level for level in supported
        if level in _EFFORT_RANK and _EFFORT_RANK[level] <= requested_rank
    ]
    if not candidates:
        # Requested level is below everything the model supports — use its floor.
        return min(supported, key=lambda level: _EFFORT_RANK[level])
    return max(candidates, key=lambda level: _EFFORT_RANK[level])
