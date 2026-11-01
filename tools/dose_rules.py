"""Shared reminder rules, independent of either state file format."""

from pathlib import Path

if __package__:
    from .common import SEOUL, ToolError
else:
    from common import SEOUL, ToolError


VOICE_PATH = Path(__file__).resolve().parents[1] / "config/voice.json"


def phase_at(scheduled_at, now):
    seconds = (now - scheduled_at).total_seconds()
    if seconds < 0:
        return 0
    if seconds < 600:
        return 1
    if seconds < 1800:
        return 2
    if seconds < 5400:
        return 3
    return "MISSED"


def is_quiet(at):
    hour = at.astimezone(SEOUL).hour
    return hour >= 23 or hour < 7


def validate_voice(voice):
    required = ("step1", "step2", "step3", "taken", "missed", "revisit")
    if not isinstance(voice, dict) or any(not isinstance(voice.get(k), str) or not voice[k] for k in required):
        raise ToolError("INVALID_VOICE", "config/voice.json의 말투를 확인해 주세요.")
    return voice
