from __future__ import annotations

from typing import Any


HERO_SEEDS = {
    "EN": (
        "Anonymous daily support when work won't let your mind rest",
        "Burnout-friendly check-ins — no diagnosis, just steadier days",
        "A calm AI companion for stress that follows you home from work",
    ),
    "UA": (
        "Анонімна підтримка, коли робота не відпускає думки",
        "Щоденні check-in проти вигорання — без діагнозів",
        "Спокійний AI-супутник для стресу після зміни",
    ),
    "DE": (
        "Anonyme tägliche Unterstützung bei Arbeitsstress",
        "Burnout-freundliche Check-ins — ohne Diagnose",
        "Ein ruhiger KI-Begleiter für Stress nach Feierabend",
    ),
}

CTA_SEEDS = {
    "EN": ("Start a calm check-in", "Try tonight's wind-down", "Talk it through anonymously"),
    "UA": ("Почати спокійний check-in", "Спробувати вечірній wind-down", "Поговорити анонімно"),
    "DE": ("Ruhigen Check-in starten", "Abend-Wind-down testen", "Anonym darüber sprechen"),
}


def generate_creative_draft_pack(*, locales: tuple[str, ...] = ("UA", "EN", "DE")) -> dict[str, Any]:
    """Creative Lab — drafts only; publish requires approval + policy gate."""
    packs = {}
    for loc in locales:
        packs[loc] = {
            "heroes": list(HERO_SEEDS.get(loc, ())),
            "ctas": list(CTA_SEEDS.get(loc, ())),
            "rsa_themes": [
                "work stress / burnout",
                "sleep anxiety (non-clinical)",
                "anonymous emotional support",
            ],
            "pmax_asset_briefs": [
                "Lifestyle calm evening commute — no medical imagery",
                "Abstract soft gradient + product UI chat bubble",
            ],
            "video_briefs": [
                "15s: overwhelm at desk → 60s anonymous check-in → calmer close",
            ],
        }
    return {
        "agent": "creative_lab",
        "mode": "auto_draft",
        "publish": "approval_required",
        "daily_target": 100,
        "generated_now": sum(len(p["heroes"]) + len(p["ctas"]) for p in packs.values()),
        "locales": packs,
        "policy": "No diagnosis, treatment, medication, crisis, or therapy-replacement claims.",
        "status": "draft_generator_v0",
    }
