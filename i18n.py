from __future__ import annotations

"""Translation table for driver-facing text (uz default, ru/en selectable via /til).

Coverage note: this wires up the mechanism and converts the most-visible driver
strings (main menu, registration, BOL confirmation) as a working example. Extending
coverage to every remaining hardcoded Uzbek string elsewhere in modules/ is a
mechanical follow-up (wrap the string here, call tr() in place of the literal) —
not a re-architecture — and is listed as a known gap rather than done silently.
"""

LANGUAGES = ("uz", "ru", "en")

TEXTS: dict[str, dict[str, str]] = {
    "menu_choose_department": {
        "uz": "Bo'limni tanlang:",
        "ru": "Выберите отдел:",
        "en": "Choose a department:",
    },
    "menu_greeting": {
        "uz": "👋 Salom, {name}!",
        "ru": "👋 Привет, {name}!",
        "en": "👋 Hi, {name}!",
    },
    "register_usage": {
        "uz": "Foydalanish: /register Ism Familiya",
        "ru": "Использование: /register Имя Фамилия",
        "en": "Usage: /register First Last",
    },
    "register_success": {
        "uz": "✅ Ro'yxatdan o'tdingiz: {name}",
        "ru": "✅ Вы зарегистрированы: {name}",
        "en": "✅ You're registered: {name}",
    },
    "register_updated": {
        "uz": "✅ Ma'lumotlar yangilandi: {name}",
        "ru": "✅ Данные обновлены: {name}",
        "en": "✅ Info updated: {name}",
    },
    "register_prompt": {
        "uz": "Avval ro'yxatdan o'ting: /register Ism Familiya",
        "ru": "Сначала зарегистрируйтесь: /register Имя Фамилия",
        "en": "Please register first: /register First Last",
    },
    "bol_prompt": {
        "uz": "📄 BOL rasmini shu caption bilan yuboring: /BOL",
        "ru": "📄 Отправьте фото BOL с подписью: /BOL",
        "en": "📄 Send the BOL photo with caption: /BOL",
    },
    "bol_accepted": {
        "uz": "✅ BOL qabul qilindi.",
        "ru": "✅ BOL принят.",
        "en": "✅ BOL accepted.",
    },
    "choose_language": {
        "uz": "🌐 Tilni tanlang:",
        "ru": "🌐 Выберите язык:",
        "en": "🌐 Choose your language:",
    },
    "language_set": {
        "uz": "✅ Til o'zbek tiliga o'rnatildi.",
        "ru": "✅ Язык установлен: русский.",
        "en": "✅ Language set to English.",
    },
}


def tr(key: str, lang: str = "uz", **kwargs) -> str:
    entry = TEXTS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("uz") or key
    return text.format(**kwargs) if kwargs else text
