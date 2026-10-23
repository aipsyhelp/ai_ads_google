"""Enterprise campaign asset coverage — Google Ads docs limits (≤20 per field type).

Fills ENABLED campaigns with brand-gated sitelinks, callouts, structured snippets,
and tops up RSA headlines toward the 15/4 RSA best-practice ceiling.
North star URLs from product_facts (LP / chat / stores).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.ads_public_urls import topic_article_url
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.product_facts import load_product_facts

logger = configure_logging()

# Google Ads API: at most 20 enabled assets per field_type per campaign.
MAX_PER_FIELD = 20
# Practical enterprise target (docs recommend 6+ sitelinks; we fill toward 20).
TARGET_SITELINKS = 16
TARGET_CALLOUTS = 16
TARGET_SNIPPETS = 4
MAX_RSA_HEADLINES = 15
MAX_LINK_TEXT = 25
MAX_DESC = 35
MAX_CALLOUT = 25


@dataclass
class CoverageResult:
    dry_run: bool
    campaigns: int = 0
    gate: dict[str, Any] = field(default_factory=dict)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    sitelinks_created: int = 0
    sitelinks_linked: int = 0
    callouts_created: int = 0
    callouts_linked: int = 0
    snippets_created: int = 0
    snippets_linked: int = 0
    rsa_topped_up: int = 0
    rsa_attempted: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    pack_preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    # Google Ads RSA rejects some unicode punctuation in mutate payloads
    t = (
        t.replace("—", "-")
        .replace("–", "-")
        .replace("…", "")
        .replace("...", "")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .strip()
    )
    if len(t) <= n:
        return t
    cut = t[:n].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return cut


def _locale_from_campaign(name: str) -> str:
    n = (name or "").upper()
    if "/ DE" in n or " /DE" in n or " DE /" in n:
        return "DE"
    if "/ LT" in n or " /LT" in n or " LT /" in n:
        return "LT"
    if "/ EN" in n or " EN /" in n or "/EN" in n:
        return "EN"
    return "UA"


def _build_sitelink_specs(facts: dict[str, Any], locale: str) -> list[dict[str, str]]:
    """Canonical sitelink pack — approved owned domains only (Ad Grants).

    Never emit App Store / Google Play / YouTube — Google rejects third-party domains.
    """
    from ads_ai_agents.intelligence.ads_public_urls import is_blocked_destination, public_urls
    from ads_ai_agents.intelligence.grant_domain_gate import is_grant_allowed_url

    u = public_urls(facts, locale=locale)
    lp = u["landing"]
    site = u["site_root"]
    news = u["news"]
    corporate = u["corporate"]
    ngo = u["ngo"]
    help_u = u.get("help_request") or site
    contact = u.get("contact") or site

    packs = {
        "UA": [
            ("Заявка", "Залишити заявку", "М'який перший крок", help_u),
            ("Контакт", "Написати команді", "Ми відповімо обережно", contact),
            ("Сервіс", "Дізнатись більше", "Велнес · не клініка", lp),
            ("Новини", "Articles та оновлення", "Корисні матеріали", news),
            ("Для команд", "Wellness для команд", "HR та співробітники", corporate),
            ("Для спільнот", "Підтримка спільнот", "Партнери та НГО", ngo),
            ("Сайт", "Головна YOUR_BRAND", "Публічна сторінка", site),
            ("Приватність", "Як ми захищаємо дані", "Прозорі умови", u.get("privacy") or site),
            ("Швидкий reset", "60-с pause", "Коли день насичений", help_u),
            ("Вечірній режим", "Вечірній спокій", "Вечір · анонімно", help_u),
            ("Підтримка змін", "Підтримка після роботи", "Без клінічних обіцянок", help_u),
            ("Після дня", "М'яка розмова", "Коли потрібна пауза", help_u),
            ("Анонімно", "Без імені в чаті", "Приватність за замовчуванням", help_u),
            ("Почати зараз", "Відкрити спокійно", "М'який перший крок", help_u),
            ("Про ГО", "Про ГО YOUR_BRAND", "Підтримка спільноти", site),
            ("Огляд сервісу", "Як працює підтримка", "Без клінічних обіцянок", help_u),
        ],
        "EN": [
            ("Help request", "Request support", "Gentle first step", help_u),
            ("Contact", "Message our team", "We reply carefully", contact),
            ("Service", "Learn more", "Wellness · not a clinic", lp),
            ("News", "Articles and updates", "Helpful reads", news),
            ("For teams", "Wellness for teams", "HR and employees", corporate),
            ("Community", "Community support", "Partners and NGOs", ngo),
            ("Public site", "YOUR_BRAND home", "Explore resources", site),
            ("Privacy", "How we protect you", "Clear terms", u.get("privacy") or site),
            ("Quick reset", "60-second pause", "When the day feels full", help_u),
            ("Evening mode", "Evening calm", "Private unwind", help_u),
            ("Shift support", "After-shift support", "No medical claims", help_u),
            ("After work", "Talk it through", "When you need a pause", help_u),
            ("Anonymous", "No name required", "Privacy by default", help_u),
            ("Start now", "Open calmly", "Gentle first step", help_u),
            ("About NGO", "About YOUR_BRAND NGO", "Community support", site),
            ("How it works", "How support works", "No medical claims", help_u),
        ],
        "DE": [
            ("Hilfe anfragen", "Unterstützung anfragen", "Sanfter Einstieg", help_u),
            ("Kontakt", "Team schreiben", "Wir antworten sorgfältig", contact),
            ("Service", "Mehr erfahren", "Wellness · keine Klinik", lp),
            ("News", "Artikel und Updates", "Hilfreiche Inhalte", news),
            ("Für Teams", "Wellness für Teams", "HR und Mitarbeitende", corporate),
            ("Community", "Community-Support", "Partner und NGOs", ngo),
            ("Website", "YOUR_BRAND Start", "Öffentliche Seite", site),
            ("Privatsphäre", "So schützen wir Daten", "Transparente Konditionen", u.get("privacy") or site),
            ("Kurzer Reset", "60-Sekunden Pause", "Am Tagesende", help_u),
            ("Abendmodus", "Abendruhe", "Am Abend · privat", help_u),
            ("Nach der Schicht", "Unterstützung danach", "Keine Medizin-Claims", help_u),
            ("Nach der Arbeit", "Darüber sprechen", "Kurze Pause", help_u),
            ("Anonym", "Ohne Namen", "Privatsphäre zuerst", help_u),
            ("Jetzt starten", "Ruhig öffnen", "Sanfter Einstieg", help_u),
            ("Mission", "Über YOUR_BRAND NGO", "Community-Unterstützung", site),
            ("So geht's", "So funktioniert Hilfe", "Keine Medizin-Claims", help_u),
        ],
    }
    raw = packs.get(locale) or packs["EN"]
    out: list[dict[str, str]] = []
    texts: list[str] = []
    for link, d1, d2, url in raw:
        if not url:
            continue
        # Ad Grants: drop any third-party / non-approved host (apple/play/youtube/…).
        if is_blocked_destination(url) or not is_grant_allowed_url(url, facts=facts):
            continue
        item = {
            "link_text": _clip(link, MAX_LINK_TEXT),
            "description1": _clip(d1, MAX_DESC),
            "description2": _clip(d2, MAX_DESC),
            "final_url": url,
        }
        texts.extend([item["link_text"], item["description1"], item["description2"]])
        out.append(item)
    gate = brand_gate_pack(texts)
    if not gate.get("pass"):
        failed = {f["text"] for f in gate.get("failed") or []}
        out = [
            x
            for x in out
            if x["link_text"] not in failed
            and x["description1"] not in failed
            and x["description2"] not in failed
        ]
    return out[:TARGET_SITELINKS]


def _build_callout_specs(locale: str) -> list[str]:
    packs = {
        "UA": [
            "Без діагнозів",
            "Приватний чат",
            "AI-підтримка 24/7",
            "М’який check-in",
            "Велнес · не клініка",
            "Публічний вебсайт",
            "Щоденник настрою",
            "Приватність first",
            "Старт за хвилину",
            "Матеріали від ГО",
            "Українською",
            "Без суджень",
            "Після робочого дня",
            "Вечірній режим",
            "Прозорі умови",
            "Підтримка спільнот",
        ],
        "EN": [
            "No diagnosis",
            "Private chat",
            "AI support 24/7",
            "Gentle check-ins",
            "Wellness not clinic",
            "Public website",
            "Mood diary",
            "Privacy first",
            "Start in a minute",
            "Resources from our NGO",
            "English ready",
            "No judgment",
            "After-work reset",
            "Evening mode",
            "Transparent terms",
            "Community support",
        ],
        "DE": [
            "Ohne Diagnose",
            "Privater Chat",
            "KI-Support 24/7",
            "Sanfte Check-ins",
            "Wellness keine Klinik",
            "Öffentliche Website",
            "Stimmungstagebuch",
            "Privatsphäre first",
            "Start in 1 Minute",
            "Materialien unserer NGO",
            "Deutsch bereit",
            "Ohne Urteil",
            "Reset nach Arbeit",
            "Abendmodus",
            "Transparente Konditionen",
            "Community-Unterstützung",
        ],
    }
    raw = [_clip(t, MAX_CALLOUT) for t in (packs.get(locale) or packs["EN"])]
    gate = brand_gate_pack(raw)
    return [t for t in (gate.get("passed_texts") or raw) if brand_gate_text(t).get("pass")][
        :TARGET_CALLOUTS
    ]


def _build_snippet_specs(locale: str) -> list[dict[str, Any]]:
    """Structured snippets — predefined headers only (Features deprecated → Service catalog)."""
    packs = {
        "UA": [
            {"header": "Types", "values": ["AI-чат", "Щоденник", "Тести", "Аналітика"]},
            {"header": "Service catalog", "values": ["Анонімність", "Check-in", "Wind-down", "Мультимова"]},
            {"header": "Styles", "values": ["Спокійний тон", "Без суду", "Короткі сесії"]},
            {"header": "Amenities", "values": ["Публічний сайт", "Новини", "Матеріали ГО"]},
        ],
        "EN": [
            {"header": "Types", "values": ["AI chat", "Diary", "Self-checks", "Analytics"]},
            {"header": "Service catalog", "values": ["Anonymous", "Check-in", "Wind-down", "Multi-locale"]},
            {"header": "Styles", "values": ["Calm tone", "No judgment", "Short sessions"]},
            {"header": "Amenities", "values": ["Public website", "News", "NGO resources"]},
        ],
        "DE": [
            {"header": "Types", "values": ["KI-Chat", "Tagebuch", "Checks", "Analytics"]},
            {"header": "Service catalog", "values": ["Anonym", "Check-in", "Wind-down", "Mehrsprachig"]},
            {"header": "Styles", "values": ["Ruhiger Ton", "Ohne Urteil", "Kurze Sessions"]},
            {"header": "Amenities", "values": ["Öffentliche Seite", "News", "NGO-Materialien"]},
        ],
    }
    return (packs.get(locale) or packs["EN"])[:TARGET_SNIPPETS]


def _rsa_hooks(locale: str) -> list[str]:
    # Mission-led generic lines. Topic relevance is supplied separately so that
    # every RSA can reach 15 unique headlines without user-facing policy jargon.
    extras = {
        "UA": (
            "YOUR_BRAND",
            "Онлайн-ресурси підтримки",
            "Матеріали від ГО",
            "Практики на щодень",
            "Анонімно та приватно",
            "Підтримка українською",
            "Корисні матеріали",
            "Дізнайтеся більше",
            "Знайдіть свою опору",
            "Щоденні практики",
            "Почніть розмову зараз",
            "Підтримка у вашому темпі",
            "Спокійний AI-чат",
            "Відкрито 24/7",
            "Без осуду",
        ),
        "EN": (
            "YOUR_BRAND",
            "Online Support Resources",
            "Resources From Our NGO",
            "Everyday Wellbeing Tools",
            "Private Online Access",
            "Practical Support Guides",
            "Explore Helpful Resources",
            "Build a Support Routine",
            "Daily Support Practices",
            "Start a Conversation",
            "Support at Your Pace",
            "Calm AI Chat",
            "Available 24/7",
            "No Judgment",
            "Voice and Text Support",
        ),
        "DE": (
            "YOUR_BRAND",
            "Online-Hilfsangebote",
            "Materialien unserer NGO",
            "Praktische Alltagstipps",
            "Anonym und privat",
            "Hilfreiche Ressourcen",
            "Mehr erfahren",
            "Tägliche Übungen",
        ),
    }
    texts = list(extras.get(locale) or extras["EN"])
    out: list[str] = []
    for t in texts:
        t = _clip(t, 30)
        if t and t not in out and brand_gate_text(t).get("pass"):
            out.append(t)
    return out


def _rsa_topic_key(ad_group_name: str) -> str:
    name = (ad_group_name or "").lower()
    markers = (
        ("depression", ("депрес", "depress")),
        ("panic", ("паніч", "panic")),
        ("anxiety", ("тривож", "anxiety", "fear", "страх")),
        ("burnout", ("вигоран", "burnout", "burnt out")),
        ("academic", ("academ", "student", "навчан")),
        ("anger", ("anger", "гнів", "зліст")),
        ("stress", ("стрес", "stress")),
        ("sleep", ("сон", "безсон", "sleep")),
        ("esteem", ("самооцін", "self esteem")),
        ("family", ("дитяч", "родин", "family", "child")),
        ("self_help", ("самодопом", "self help", "breathing")),
        ("support", ("допомог", "counsel", "mental", "psycholog")),
    )
    for key, terms in markers:
        if any(term in name for term in terms):
            return key
    return "support"


def _rsa_topic_hooks(locale: str, ad_group_name: str) -> list[str]:
    """Return topic-rich educational copy without treatment or outcome claims."""
    packs = {
        "UA": {
            "depression": (
                "Матеріали про депресію",
                "Як підтримати себе",
                "Поради для складних днів",
                "Щоденні кроки підтримки",
                "Практики самопідтримки",
                "Опора у складні моменти",
                "Корисні матеріали від ГО",
                "Доступна онлайн-підтримка",
            ),
            "panic": (
                "Про панічні атаки",
                "Поради у тривожний момент",
                "Техніки заземлення",
                "Дихальні практики",
                "Як повернути відчуття опори",
                "Матеріали для самопідтримки",
                "Спокійні щоденні практики",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "anxiety": (
                "Матеріали про тривогу",
                "Як впоратися з тривогою",
                "Опора у тривожні моменти",
                "Техніки заземлення",
                "Практики для спокою",
                "Щоденна самопідтримка",
                "Поради від ГО YOUR_BRAND",
                "Ресурси про тривожність",
            ),
            "stress": (
                "Поради при стресі",
                "Як впоратися зі стресом",
                "Практики проти стресу",
                "Опора у напружені дні",
                "Щоденні вправи для спокою",
                "Матеріали про стрес",
                "Відновіть щоденний баланс",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "burnout": (
                "Матеріали про вигорання",
                "Як впоратися з вигоранням",
                "Ознаки емоційного вигорання",
                "Опора при перевтомі",
                "Баланс після роботи",
                "Практики для відновлення",
                "Поради про робочий баланс",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "academic": (
                "Поради при стресі в навчанні",
                "Баланс навчання і відпочинку",
                "Практики для студентів",
                "Як зменшити напруження",
                "Щоденна підтримка студентів",
                "Матеріали про навчальний стрес",
                "Опора під час іспитів",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "anger": (
                "Як впоратися з гнівом",
                "Практики керування емоціями",
                "Пауза у напружений момент",
                "Щоденні техніки самоконтролю",
                "Матеріали про сильні емоції",
                "Спокійні дихальні практики",
                "Опора у складній розмові",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "sleep": (
                "Поради для кращого сну",
                "Спокійний вечірній ритм",
                "Практики перед сном",
                "Матеріали про здоровий сон",
                "Вечірні вправи для спокою",
                "Як створити рутину сну",
                "Щоденна турбота про сон",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "esteem": (
                "Підтримка самооцінки",
                "Практики самопідтримки",
                "Щоденні кроки впевненості",
                "Матеріали про самооцінку",
                "М'яка щоденна опора",
                "Як підтримати себе",
                "Корисні вправи на щодень",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "family": (
                "Підтримка для родин",
                "Матеріали для батьків",
                "Практики для всієї родини",
                "Опора у сімейних розмовах",
                "Поради для складних днів",
                "Безпечна онлайн-підтримка",
                "Щоденні сімейні практики",
                "Ресурси від ГО YOUR_BRAND",
            ),
            "support": (
                "Доступна онлайн-підтримка",
                "Підтримка від ГО",
                "Як знайти емоційну опору",
                "Матеріали для самопідтримки",
                "Щоденні практики для себе",
                "Анонімні онлайн-ресурси",
                "Корисні поради від YOUR_BRAND",
                "Зробіть перший крок",
            ),
            "self_help": (
                "Техніки самодопомоги",
                "Практики на щодень",
                "Матеріали для самопідтримки",
                "Дихальні вправи для спокою",
                "Щоденні кроки підтримки",
                "Як повернути відчуття опори",
                "Поради від ГО YOUR_BRAND",
                "М'який перший крок",
            ),
        },
        "EN": {
            "depression": (
                "Depression Support Resources",
                "Support on Difficult Days",
                "Everyday Self-Support",
                "Practical Wellbeing Guides",
                "Build a Daily Support Routine",
                "Resources From Our NGO",
                "Find Helpful Next Steps",
                "Private Online Resources",
            ),
            "panic": (
                "Panic Attack Resources",
                "Grounding Techniques",
                "Calming Breathing Practices",
                "Support for Anxious Moments",
                "Regain a Sense of Balance",
                "Everyday Self-Support",
                "Practical Guides From Our NGO",
                "Private Online Resources",
            ),
            "anxiety": (
                "Anxiety Support Resources",
                "Ways to Manage Anxiety",
                "Grounding Techniques",
                "Calming Daily Practices",
                "Support for Anxious Moments",
                "Everyday Self-Support",
                "Practical Guides From Our NGO",
                "Private Online Resources",
            ),
            "stress": (
                "Stress Support Resources",
                "Ways to Manage Stress",
                "Practical Stress Guides",
                "Support on Difficult Days",
                "Calming Daily Practices",
                "Build a Balanced Routine",
                "Resources From Our NGO",
                "Private Online Resources",
            ),
            "burnout": (
                "Burnout Support Resources",
                "Ways to Manage Burnout",
                "Workplace Burnout Signs",
                "Support for Exhaustion",
                "Build After-Work Balance",
                "Daily Recovery Practices",
                "Practical Burnout Guides",
                "Resources From Our NGO",
            ),
            "academic": (
                "Academic Stress Resources",
                "Ways to Manage Study Stress",
                "Study and Rest Balance",
                "Everyday Student Support",
                "Support During Exam Season",
                "Calming Study Practices",
                "Practical Guides for Students",
                "Resources From Our NGO",
            ),
            "anger": (
                "Ways to Manage Anger",
                "Emotional Regulation Guides",
                "Pause in a Tense Moment",
                "Everyday Coping Practices",
                "Resources for Strong Emotions",
                "Calming Breathing Practices",
                "Support for Hard Conversations",
                "Resources From Our NGO",
            ),
            "sleep": (
                "Resources for Better Sleep",
                "A Calmer Evening Routine",
                "Before-Sleep Practices",
                "Build a Healthy Sleep Routine",
                "Evening Calming Exercises",
                "Everyday Sleep Support",
                "Practical Sleep Guides",
                "Resources From Our NGO",
            ),
            "esteem": (
                "Self-Esteem Support",
                "Everyday Confidence Practices",
                "Gentle Daily Support",
                "Self-Support Practices",
                "Practical Self-Esteem Guides",
                "Build a Supportive Routine",
                "Helpful Everyday Exercises",
                "Resources From Our NGO",
            ),
            "family": (
                "Support Resources for Families",
                "Resources for Parents",
                "Everyday Family Practices",
                "Support for Family Talks",
                "Guides for Difficult Days",
                "Private Online Support",
                "Build a Family Routine",
                "Resources From Our NGO",
            ),
            "support": (
                "Accessible Online Support",
                "Support From Our NGO",
                "Find Emotional Support",
                "Everyday Self-Support",
                "Practical Wellbeing Guides",
                "Private Online Resources",
                "Helpful Next Steps",
                "Start With a Small Step",
            ),
            "self_help": (
                "Self-Help Techniques",
                "Everyday Support Practices",
                "Practical Self-Support Guides",
                "Calming Breathing Exercises",
                "Build a Daily Support Routine",
                "Regain a Sense of Balance",
                "Resources From Our NGO",
                "Start With a Small Step",
            ),
        },
    }
    lang = "UA" if locale == "UA" else "EN"
    selected = packs[lang][_rsa_topic_key(ad_group_name)]
    return [text for text in selected if len(text) <= 30 and brand_gate_text(text).get("pass")]


def _rsa_descriptions(locale: str, ad_group_name: str = "") -> list[str]:
    topic = _rsa_topic_key(ad_group_name)
    topic_lines = {
        "UA": {
            "depression": "Читайте матеріали про депресію та щоденні практики самопідтримки від ГО.",
            "panic": "Дізнайтеся про панічні атаки, заземлення та спокійні дихальні практики.",
            "anxiety": "Знайдіть матеріали про тривогу, заземлення та щоденну самопідтримку.",
            "stress": "Читайте практичні матеріали про стрес, баланс і щоденні вправи для спокою.",
            "burnout": "Дізнайтеся про ознаки вигорання, робочий баланс і практики відновлення.",
            "academic": "Знайдіть поради про навчальний стрес, відпочинок і підтримку під час іспитів.",
            "anger": "Читайте про сильні емоції, паузу та практики керування гнівом на щодень.",
            "sleep": "Знайдіть матеріали про здоровий сон, вечірній ритм і практики перед сном.",
            "esteem": "Читайте про самооцінку, впевненість і щоденні практики самопідтримки.",
            "family": "Знайдіть матеріали для батьків і практики підтримки для всієї родини.",
            "self_help": "Спробуйте доступні техніки самодопомоги та щоденні вправи для спокою.",
            "support": "Знайдіть доступні матеріали та щоденні практики емоційної підтримки.",
        },
        "EN": {
            "depression": "Explore depression resources and everyday self-support practices from our NGO.",
            "panic": "Learn about panic attacks, grounding techniques and calming breathing practices.",
            "anxiety": "Explore anxiety resources, grounding techniques and everyday self-support.",
            "stress": "Find practical stress guides, daily calming exercises and balance resources.",
            "burnout": "Learn about burnout signs, workplace balance and everyday recovery practices.",
            "academic": "Find academic stress guides, study-rest balance and support during exam season.",
            "anger": "Explore strong emotions, mindful pauses and everyday anger management practices.",
            "sleep": "Find better-sleep resources, evening routines and calming before-sleep practices.",
            "esteem": "Explore self-esteem resources, confidence practices and gentle daily support.",
            "family": "Find resources for parents and everyday support practices for the whole family.",
            "self_help": "Explore practical self-help techniques and everyday calming exercises.",
            "support": "Find accessible resources and everyday practices for emotional support.",
        },
    }
    generic = {
        "UA": (
            "Практичні ресурси від ГО YOUR_BRAND доступні анонімно на www.example.com.",
            "Оберіть корисні матеріали та сформуйте власну щоденну рутину підтримки.",
            "Місія ГО — робити емоційну підтримку доступнішою для кожної людини.",
        ),
        "EN": (
            "Practical resources from YOUR_BRAND NGO are privately available at www.example.com.",
            "Explore helpful materials and build an everyday support routine that fits you.",
            "Our NGO mission is to make emotional support more accessible for everyone.",
        ),
    }
    lang = "UA" if locale == "UA" else "EN"
    texts = (topic_lines[lang][topic],) + generic[lang]
    return [text for text in texts if len(text) <= 90 and brand_gate_text(text).get("pass")]


class EnterpriseAssetCoverage:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_product_facts()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="asset_publish")
        return self._client

    def audit(self) -> dict[str, Any]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        camps: dict[int, dict[str, Any]] = {}
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.status
            FROM campaign WHERE campaign.status = 'ENABLED'
            """,
        ):
            camps[r.campaign.id] = {
                "id": r.campaign.id,
                "name": r.campaign.name,
                "type": str(r.campaign.advertising_channel_type).split(".")[-1],
                "locale": _locale_from_campaign(r.campaign.name),
                "assets": Counter(),
            }
        ft_enum = self.client.enums.AssetFieldTypeEnum
        ft_names = {int(getattr(ft_enum, n)): n for n in dir(ft_enum) if n.isupper()}
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.status, campaign_asset.field_type, campaign_asset.status
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED' AND campaign_asset.status != 'REMOVED'
            """,
        ):
            if r.campaign.id not in camps:
                continue
            raw = int(r.campaign_asset.field_type)
            ft = ft_names.get(raw) or str(raw)
            camps[r.campaign.id]["assets"][ft] += 1

        rsa_fill: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.status,
                   ad_group_ad.ad.responsive_search_ad.headlines,
                   ad_group_ad.ad.responsive_search_ad.descriptions
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
            """,
        ):
            h = len(list(r.ad_group_ad.ad.responsive_search_ad.headlines or []))
            d = len(list(r.ad_group_ad.ad.responsive_search_ad.descriptions or []))
            rsa_fill[r.campaign.id].append((h, d))

        rows = []
        for camp in camps.values():
            assets = dict(camp["assets"])
            rsa = rsa_fill.get(camp["id"]) or []
            avg_h = round(sum(x[0] for x in rsa) / len(rsa), 1) if rsa else 0
            rows.append(
                {
                    "id": camp["id"],
                    "name": camp["name"],
                    "type": camp["type"],
                    "locale": camp["locale"],
                    "sitelinks": assets.get("SITELINK", 0),
                    "callouts": assets.get("CALLOUT", 0),
                    "snippets": assets.get("STRUCTURED_SNIPPET", 0),
                    "ad_images": assets.get("AD_IMAGE", 0),
                    "rsa_count": len(rsa),
                    "rsa_avg_headlines": avg_h,
                    "gaps": {
                        "sitelinks_to_target": max(0, TARGET_SITELINKS - assets.get("SITELINK", 0)),
                        "callouts_to_target": max(0, TARGET_CALLOUTS - assets.get("CALLOUT", 0)),
                        "snippets_to_target": max(0, TARGET_SNIPPETS - assets.get("STRUCTURED_SNIPPET", 0)),
                        "rsa_headlines_short": sum(1 for h, _ in rsa if h < MAX_RSA_HEADLINES),
                    },
                }
            )
        return {
            "standard": "Google Ads API ≤20 assets/field_type/campaign; RSA ≤15H/4D",
            "targets": {
                "sitelinks": TARGET_SITELINKS,
                "callouts": TARGET_CALLOUTS,
                "snippets": TARGET_SNIPPETS,
                "rsa_headlines": MAX_RSA_HEADLINES,
            },
            "campaigns": rows,
        }

    def apply(self, *, dry_run: bool = True, top_up_rsa: bool = True, max_rsa: int = 200) -> CoverageResult:
        audit = self.audit()
        all_texts: list[str] = []
        for loc in ("UA", "EN", "DE"):
            for s in _build_sitelink_specs(self.facts, loc):
                all_texts.extend([s["link_text"], s["description1"], s["description2"]])
            all_texts.extend(_build_callout_specs(loc))
            all_texts.extend(_rsa_hooks(loc))
        gate = brand_gate_pack(all_texts)
        result = CoverageResult(
            dry_run=dry_run,
            campaigns=len(audit["campaigns"]),
            gate=gate,
            before=audit,
            pack_preview={
                loc: {
                    "sitelinks": _build_sitelink_specs(self.facts, loc),
                    "callouts": _build_callout_specs(loc),
                    "snippets": _build_snippet_specs(loc),
                }
                for loc in ("UA", "EN", "DE")
            },
        )
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result
        if not dry_run:
            assert_mutations_allowed()

        # Prefer library assets (link-only) before create — saves explorer ops.
        sitelink_rn: dict[tuple[str, str], str] = {}
        callout_rn: dict[str, str] = {}
        snippet_rn: dict[tuple[str, tuple[str, ...]], str] = {}
        if not dry_run:
            sitelink_rn.update(self._library_sitelinks())
            callout_rn.update(self._library_callouts())
            snippet_rn.update(self._library_snippets())

        for camp in audit["campaigns"]:
            locale = camp["locale"]
            camp_rn = f"customers/{self.settings.customer_id}/campaigns/{camp['id']}"
            try:
                self._fill_sitelinks(camp, camp_rn, locale, sitelink_rn, result, dry_run=dry_run)
                self._fill_callouts(camp, camp_rn, locale, callout_rn, result, dry_run=dry_run)
                self._fill_snippets(camp, camp_rn, locale, snippet_rn, result, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"campaign": camp["name"], "error": str(exc)[:240]})

        if top_up_rsa:
            self._top_up_rsa(result, dry_run=dry_run, max_rsa=max_rsa)

        result.after = self.audit() if not dry_run else audit
        return result

    def _create_sitelink(self, spec: dict[str, str], *, dry_run: bool) -> str | None:
        if dry_run:
            return f"dry_run/sitelink/{spec['link_text']}"
        asset_svc = self.client.get_service("AssetService")
        op = self.client.get_type("AssetOperation")
        asset = op.create
        asset.name = f"SL · {spec['link_text']}"
        asset.sitelink_asset.link_text = spec["link_text"]
        asset.sitelink_asset.description1 = spec["description1"]
        asset.sitelink_asset.description2 = spec["description2"]
        assert_grant_url(spec["final_url"], facts=self.facts)
        asset.final_urls.append(spec["final_url"])
        resp = asset_svc.mutate_assets(
            customer_id=self.settings.customer_id, operations=[op]
        )
        return resp.results[0].resource_name

    def _create_callout(self, text: str, *, dry_run: bool) -> str | None:
        if dry_run:
            return f"dry_run/callout/{text}"
        asset_svc = self.client.get_service("AssetService")
        op = self.client.get_type("AssetOperation")
        op.create.name = f"CO · {text}"
        op.create.callout_asset.callout_text = text
        resp = asset_svc.mutate_assets(
            customer_id=self.settings.customer_id, operations=[op]
        )
        return resp.results[0].resource_name

    def _create_snippet(self, spec: dict[str, Any], *, dry_run: bool) -> str | None:
        if dry_run:
            return f"dry_run/snippet/{spec['header']}"
        asset_svc = self.client.get_service("AssetService")
        op = self.client.get_type("AssetOperation")
        op.create.name = f"SS · {spec['header']}"
        op.create.structured_snippet_asset.header = spec["header"]
        op.create.structured_snippet_asset.values.extend(list(spec["values"]))
        resp = asset_svc.mutate_assets(
            customer_id=self.settings.customer_id, operations=[op]
        )
        return resp.results[0].resource_name

    def _link_campaign_asset(
        self, campaign_rn: str, asset_rn: str, field_type: str, *, dry_run: bool
    ) -> None:
        if dry_run:
            return
        ca_svc = self.client.get_service("CampaignAssetService")
        op = self.client.get_type("CampaignAssetOperation")
        op.create.campaign = campaign_rn
        op.create.asset = asset_rn
        op.create.field_type = getattr(self.client.enums.AssetFieldTypeEnum, field_type)
        ca_svc.mutate_campaign_assets(
            customer_id=self.settings.customer_id, operations=[op]
        )

    def _library_sitelinks(self) -> dict[tuple[str, str], str]:
        """Map (link_text_lower, final_url) → asset resource_name from account library."""
        ga = self.client.get_service("GoogleAdsService")
        out: dict[tuple[str, str], str] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT asset.resource_name, asset.sitelink_asset.link_text, asset.final_urls
            FROM asset
            WHERE asset.type = 'SITELINK'
            """,
        ):
            text = (r.asset.sitelink_asset.link_text or "").strip().lower()
            urls = list(r.asset.final_urls or [])
            url = (urls[0] if urls else "").strip()
            if text and url and (text, url) not in out:
                out[(text, url)] = r.asset.resource_name
        return out

    def _library_callouts(self) -> dict[str, str]:
        ga = self.client.get_service("GoogleAdsService")
        out: dict[str, str] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT asset.resource_name, asset.callout_asset.callout_text
            FROM asset
            WHERE asset.type = 'CALLOUT'
            """,
        ):
            text = (r.asset.callout_asset.callout_text or "").strip().lower()
            if text and text not in out:
                out[text] = r.asset.resource_name
        return out

    def _library_snippets(self) -> dict[tuple[str, tuple[str, ...]], str]:
        ga = self.client.get_service("GoogleAdsService")
        out: dict[tuple[str, tuple[str, ...]], str] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT asset.resource_name, asset.structured_snippet_asset.header,
                   asset.structured_snippet_asset.values
            FROM asset
            WHERE asset.type = 'STRUCTURED_SNIPPET'
            """,
        ):
            header = (r.asset.structured_snippet_asset.header or "").strip()
            values = tuple(r.asset.structured_snippet_asset.values or [])
            key = (header, values)
            if header and key not in out:
                out[key] = r.asset.resource_name
        return out

    def _existing_sitelink_keys(self, campaign_id: int) -> set[str]:
        ga = self.client.get_service("GoogleAdsService")
        keys: set[str] = set()
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query=f"""
            SELECT campaign.id, campaign.status, asset.sitelink_asset.link_text, asset.final_urls
            FROM campaign_asset
            WHERE campaign.id = {campaign_id} AND campaign.status = 'ENABLED'
              AND campaign_asset.field_type = SITELINK
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            keys.add((r.asset.sitelink_asset.link_text or "").strip().lower())
        return keys

    def _existing_callout_keys(self, campaign_id: int) -> set[str]:
        ga = self.client.get_service("GoogleAdsService")
        keys: set[str] = set()
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query=f"""
            SELECT campaign.id, campaign.status, asset.callout_asset.callout_text
            FROM campaign_asset
            WHERE campaign.id = {campaign_id} AND campaign.status = 'ENABLED'
              AND campaign_asset.field_type = CALLOUT
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            keys.add((r.asset.callout_asset.callout_text or "").strip().lower())
        return keys

    def _existing_snippet_headers(self, campaign_id: int) -> set[str]:
        ga = self.client.get_service("GoogleAdsService")
        keys: set[str] = set()
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query=f"""
            SELECT campaign.id, campaign.status, asset.structured_snippet_asset.header
            FROM campaign_asset
            WHERE campaign.id = {campaign_id} AND campaign.status = 'ENABLED'
              AND campaign_asset.field_type = STRUCTURED_SNIPPET
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            keys.add((r.asset.structured_snippet_asset.header or "").strip().lower())
        return keys

    def _fill_sitelinks(
        self,
        camp: dict[str, Any],
        camp_rn: str,
        locale: str,
        cache: dict[tuple[str, str], str],
        result: CoverageResult,
        *,
        dry_run: bool,
    ) -> None:
        current = camp["sitelinks"]
        need = min(MAX_PER_FIELD - current, max(0, TARGET_SITELINKS - current))
        if need <= 0:
            result.skipped.append(f"sitelinks_full:{camp['name'][:40]}")
            return
        existing = self._existing_sitelink_keys(camp["id"])
        for spec in _build_sitelink_specs(self.facts, locale):
            if need <= 0:
                break
            key = (spec["link_text"].lower(), spec["final_url"])
            if spec["link_text"].lower() in existing:
                continue
            rn = cache.get(key)
            if not rn:
                try:
                    rn = self._create_sitelink(spec, dry_run=dry_run)
                    if rn:
                        cache[key] = rn
                        result.sitelinks_created += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {"op": "create_sitelink", "text": spec["link_text"], "error": str(exc)[:200]}
                    )
                    continue
            try:
                self._link_campaign_asset(camp_rn, rn, "SITELINK", dry_run=dry_run)
                result.sitelinks_linked += 1
                existing.add(spec["link_text"].lower())
                need -= 1
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "already" in msg or "duplicate" in msg or "exists" in msg:
                    result.skipped.append(f"sitelink_dup:{spec['link_text']}")
                else:
                    result.errors.append(
                        {"op": "link_sitelink", "campaign": camp["name"][:40], "error": str(exc)[:200]}
                    )

    def _fill_callouts(
        self,
        camp: dict[str, Any],
        camp_rn: str,
        locale: str,
        cache: dict[str, str],
        result: CoverageResult,
        *,
        dry_run: bool,
    ) -> None:
        current = camp["callouts"]
        need = min(MAX_PER_FIELD - current, max(0, TARGET_CALLOUTS - current))
        if need <= 0:
            result.skipped.append(f"callouts_full:{camp['name'][:40]}")
            return
        existing = self._existing_callout_keys(camp["id"])
        for text in _build_callout_specs(locale):
            if need <= 0:
                break
            if text.lower() in existing:
                continue
            rn = cache.get(text.lower())
            if not rn:
                try:
                    rn = self._create_callout(text, dry_run=dry_run)
                    if rn:
                        cache[text.lower()] = rn
                        result.callouts_created += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append({"op": "create_callout", "text": text, "error": str(exc)[:200]})
                    continue
            try:
                self._link_campaign_asset(camp_rn, rn, "CALLOUT", dry_run=dry_run)
                result.callouts_linked += 1
                existing.add(text.lower())
                need -= 1
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "already" in msg or "duplicate" in msg:
                    result.skipped.append(f"callout_dup:{text}")
                else:
                    result.errors.append(
                        {"op": "link_callout", "campaign": camp["name"][:40], "error": str(exc)[:200]}
                    )

    def _fill_snippets(
        self,
        camp: dict[str, Any],
        camp_rn: str,
        locale: str,
        cache: dict[tuple[str, tuple[str, ...]], str],
        result: CoverageResult,
        *,
        dry_run: bool,
    ) -> None:
        current = camp["snippets"]
        need = min(MAX_PER_FIELD - current, max(0, TARGET_SNIPPETS - current))
        if need <= 0:
            result.skipped.append(f"snippets_full:{camp['name'][:40]}")
            return
        existing = self._existing_snippet_headers(camp["id"])
        for spec in _build_snippet_specs(locale):
            if need <= 0:
                break
            if spec["header"].lower() in existing:
                continue
            key = (spec["header"], tuple(spec["values"]))
            rn = cache.get(key)
            if not rn:
                try:
                    rn = self._create_snippet(spec, dry_run=dry_run)
                    if rn:
                        cache[key] = rn
                        result.snippets_created += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {"op": "create_snippet", "header": spec["header"], "error": str(exc)[:200]}
                    )
                    continue
            try:
                self._link_campaign_asset(camp_rn, rn, "STRUCTURED_SNIPPET", dry_run=dry_run)
                result.snippets_linked += 1
                existing.add(spec["header"].lower())
                need -= 1
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "already" in msg or "duplicate" in msg:
                    result.skipped.append(f"snippet_dup:{spec['header']}")
                else:
                    result.errors.append(
                        {"op": "link_snippet", "campaign": camp["name"][:40], "error": str(exc)[:200]}
                    )

    def _top_up_rsa(self, result: CoverageResult, *, dry_run: bool, max_rsa: int) -> None:
        from google.protobuf import field_mask_pb2

        from ads_ai_agents.intelligence.asset_publisher import _is_clinical_headline

        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        ad_service = None if dry_run else self.client.get_service("AdService")
        rows = list(
            ga.search(
                customer_id=cid,
                query="""
                SELECT campaign.name, campaign.status, ad_group.name,
                       ad_group_ad.ad.id,
                       ad_group_ad.ad.final_urls,
                       ad_group_ad.ad.responsive_search_ad.headlines,
                       ad_group_ad.ad.responsive_search_ad.descriptions
                FROM ad_group_ad
                WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
                  AND ad_group_ad.status = 'ENABLED'
                  AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
                """,
            )
        )
        for row in rows:
            if result.rsa_attempted >= max_rsa:
                break
            locale = _locale_from_campaign(row.campaign.name)
            target_url = topic_article_url(locale, row.ad_group.name)
            current_urls = list(row.ad_group_ad.ad.final_urls or [])
            existing = [(h.text or "").strip() for h in (row.ad_group_ad.ad.responsive_search_ad.headlines or [])]
            existing_descriptions = [
                (d.text or "").strip()
                for d in (row.ad_group_ad.ad.responsive_search_ad.descriptions or [])
            ]
            descriptions = _rsa_descriptions(locale, row.ad_group.name)
            customizers = [
                t
                for t in existing
                if "{" in t and "}" in t and t.count("{") == t.count("}") and not _is_clinical_headline(t)
            ]
            kept = [
                t
                for t in existing
                if t not in customizers and not _is_clinical_headline(t) and brand_gate_text(t).get("pass")
            ]
            merged: list[str] = []
            for t in customizers + _rsa_topic_hooks(locale, row.ad_group.name):
                if t and t not in merged:
                    merged.append(t)
            for t in _rsa_hooks(locale) + kept:
                if "{" in t or "}" in t:
                    continue
                if t not in merged and brand_gate_text(t).get("pass"):
                    merged.append(t)
                if len(merged) >= MAX_RSA_HEADLINES:
                    break
            if len(merged) < 3:
                result.skipped.append(f"rsa_too_thin:{row.ad_group_ad.ad.id}")
                continue
            if (
                len(merged) == len(existing)
                and set(merged) == set(existing)
                and existing_descriptions == descriptions
                and [url.rstrip("/") for url in current_urls]
                == [target_url.rstrip("/")]
            ):
                continue
            result.rsa_attempted += 1
            if dry_run:
                result.rsa_topped_up += 1
                continue
            try:
                ad_op = self.client.get_type("AdOperation")
                ad_op.update.resource_name = f"customers/{cid}/ads/{row.ad_group_ad.ad.id}"
                rsa = ad_op.update.responsive_search_ad
                del rsa.headlines[:]
                for text in merged[:MAX_RSA_HEADLINES]:
                    asset = self.client.get_type("AdTextAsset")
                    asset.text = text
                    rsa.headlines.append(asset)
                del rsa.descriptions[:]
                for text in descriptions:
                    asset = self.client.get_type("AdTextAsset")
                    asset.text = text
                    rsa.descriptions.append(asset)
                update_paths = [
                    "responsive_search_ad.headlines",
                    "responsive_search_ad.descriptions",
                ]
                assert_grant_url(target_url, facts=self.facts)
                del ad_op.update.final_urls[:]
                ad_op.update.final_urls.append(target_url)
                update_paths.append("final_urls")
                ad_op.update_mask.CopyFrom(
                    field_mask_pb2.FieldMask(paths=update_paths)
                )
                ad_service.mutate_ads(customer_id=cid, operations=[ad_op])
                result.rsa_topped_up += 1
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                # Already-PROHIBITED ads: rewrite with wellness hooks only (drop customizers)
                if "prohibited" in msg or "disapproved" in msg:
                    clean = [t for t in _rsa_hooks(locale) if "{" not in t][:MAX_RSA_HEADLINES]
                    if len(clean) < 3:
                        result.errors.append(
                            {"op": "rsa_topup", "campaign": row.campaign.name[:40], "error": str(exc)[:200]}
                        )
                        continue
                    try:
                        ad_op = self.client.get_type("AdOperation")
                        ad_op.update.resource_name = f"customers/{cid}/ads/{row.ad_group_ad.ad.id}"
                        rsa = ad_op.update.responsive_search_ad
                        del rsa.headlines[:]
                        for text in clean:
                            asset = self.client.get_type("AdTextAsset")
                            asset.text = text
                            rsa.headlines.append(asset)
                        del rsa.descriptions[:]
                        for text in descriptions:
                            asset = self.client.get_type("AdTextAsset")
                            asset.text = text
                            rsa.descriptions.append(asset)
                        ad_op.update_mask.CopyFrom(
                            field_mask_pb2.FieldMask(
                                paths=[
                                    "responsive_search_ad.headlines",
                                    "responsive_search_ad.descriptions",
                                ]
                            )
                        )
                        ad_service.mutate_ads(customer_id=cid, operations=[ad_op])
                        result.rsa_topped_up += 1
                        continue
                    except Exception as exc2:  # noqa: BLE001
                        result.errors.append(
                            {
                                "op": "rsa_topup_clean",
                                "campaign": row.campaign.name[:40],
                                "error": str(exc2)[:200],
                            }
                        )
                        continue
                result.errors.append(
                    {"op": "rsa_topup", "campaign": row.campaign.name[:40], "error": str(exc)[:200]}
                )
