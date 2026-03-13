import json
import os
import re
from difflib import SequenceMatcher
from threading import Lock
from typing import Any, Dict, List, Optional

SEED_VERSION = "2026-03-13-taxonomy-v1"


DEFAULT_CATEGORY_CATALOG: List[Dict[str, Any]] = [
    {
        "canonical_name": "Продукти",
        "description": "Базові продукти для дому: м'ясо, бакалія, овочі, фрукти, молочка, яйця, хліб.",
        "aliases": [
            "продукти", "їжа додому", "бакалія", "крупи", "макарони", "гречка", "рис", "борошно", "цукор",
            "овочі", "фрукти", "яблука", "банани", "помідори", "огірки", "картопля", "цибуля", "морква",
            "м'ясо", "курка", "фарш", "свинина", "яловичина", "риба", "хліб", "батон", "булка",
            "молоко", "кефір", "йогурт", "сир", "сири", "творог", "яйця", "масло", "олія", "соняшникова олія",
            "консерви", "спеції", "соус", "кетчуп", "майонез"
        ],
        "examples": ["курка", "гречка", "помідори", "яйця", "хліб"],
    },
    {
        "canonical_name": "Вода",
        "description": "Питна або мінеральна вода без цукру, у тому числі газована мінеральна вода.",
        "aliases": [
            "вода", "питна вода", "мінеральна вода", "газована вода", "негазована вода", "мінералка",
            "morshynska", "моршинська", "боржомі", "borjomi", "карпатська джерельна", "карпатська", "bonaqua", "bon aqua",
            "aqua life", "аква лайф"
        ],
        "examples": ["Моршинська", "Bonaqua", "Borjomi"],
    },
    {
        "canonical_name": "Солодкі напої",
        "description": "Кола, спрайт, лимонади, соки, холодні чаї, енергетики та інші солодкі напої.",
        "aliases": [
            "солодкі напої", "напої", "газовані напої", "солодка газована вода", "сік", "соки", "нектар",
            "енергетик", "енергетики", "лимонад", "холодний чай", "iced tea", "cola", "coca cola", "coca-cola",
            "pepsi", "sprite", "fanta", "живчик", "7up", "schweppes", "mirinda", "burn", "red bull", "monster",
            "monster energy", "non stop"
        ],
        "examples": ["Coca-Cola", "Pepsi", "Sprite", "Red Bull"],
    },
    {
        "canonical_name": "Алкоголь",
        "description": "Будь-який алкоголь: пиво, вино, віскі, горілка, ром, джин, сидр.",
        "aliases": [
            "алкоголь", "пиво", "beer", "вино", "wine", "сидр", "шампанське", "ігристе", "віскі", "whisky",
            "горілка", "водка", "ром", "джин", "gin", "коньяк", "бренді", "ликер", "лікер", "текила"
        ],
        "examples": ["пиво", "віскі", "вино"],
    },
    {
        "canonical_name": "Фастфуд і снеки",
        "description": "Швидка їжа, готові перекуси, напівфабрикати, солодощі, чіпси, насіння та інші снеки.",
        "aliases": [
            "фастфуд", "фастфуд і снеки", "готова їжа", "напівфабрикати", "перекус", "снеки", "снеки та солодощі",
            "доставка фастфуду", "бургер", "бургер меню", "хот дог", "hot dog", "шаурма", "донер", "піца",
            "бутерброд", "сендвіч", "сосиски", "ковбаса", "пельмені", "вареники", "нагетси", "нагетси", "фрі",
            "чіпси", "chips", "сухарики", "насіння", "солодощі", "шоколад", "батончик", "цукерки", "печиво",
            "сирок", "круасан", "лаваш", "рол з куркою", "готовий обід", "кулінарія", "mcdonald", "kfc", "burger king"
        ],
        "examples": ["хот дог", "пельмені", "чіпси", "шоколад"],
    },
    {
        "canonical_name": "Кафе та ресторани",
        "description": "Повноцінні витрати в кафе, ресторанах, барах, пабах, кав'ярнях та подібних закладах.",
        "aliases": [
            "кафе та ресторани", "ресторан", "ресторани", "кафе", "кав'ярня", "кофейня", "coffee shop", "паб", "суші бар",
            "піцерія", "бар ресторан", "обід у закладі", "вечеря у закладі", "рахунок ресторан", "чек ресторан", "latte", "cappuccino",
            "капучино", "лате", "американо", "еспресо", "матча латте"
        ],
        "examples": ["ресторан", "кав'ярня", "капучино", "паб"],
    },
    {
        "canonical_name": "Аптека",
        "description": "Ліки, таблетки, мазі, вітаміни, сиропи та аптечні товари.",
        "aliases": [
            "аптека", "ліки", "таблетки", "таблетка", "мазь", "сироп", "вітаміни", "каплі", "антибіотик", "ібупрофен",
            "ibuprofen", "парацетамол", "цитрамон", "нурофен", "аспірин", "термометр", "пластир", "бинт"
        ],
        "examples": ["Нурофен", "вітаміни", "каплі"],
    },
    {
        "canonical_name": "Медичні послуги",
        "description": "Лікарі, клініки, аналізи, обстеження, стоматологія, прийоми та процедури.",
        "aliases": [
            "медичні послуги", "лікарня", "клініка", "лікар", "прийом лікаря", "аналізи", "обстеження", "узд", "узі",
            "стоматолог", "стоматологія", "медична процедура", "процедури", "медичний центр", "рентген", "томографія"
        ],
        "examples": ["аналізи", "прийом лікаря", "стоматологія"],
    },
    {
        "canonical_name": "Тварини",
        "description": "Усе для тварин: корм, ліки, ветеринар, наповнювач, кінолог, аксесуари.",
        "aliases": [
            "тварини", "для тварин", "корм для тварин", "корм", "ветеринар", "ветклініка", "ветаптека", "ліки для тварин",
            "наповнювач", "повідець", "нашийник", "іграшка для собаки", "іграшка для кота", "кінолог", "грумінг", "pet",
            "dog food", "cat food", "кішк", "собак"
        ],
        "examples": ["корм", "ветеринар", "кінолог"],
    },
    {
        "canonical_name": "Гігієна та догляд",
        "description": "Особиста гігієна і базовий догляд: шампунь, гель, крем, паста, прокладки, туалетний папір.",
        "aliases": [
            "гігієна та догляд", "гігієна", "косметика", "догляд", "особиста гігієна", "шампунь", "бальзам", "кондиціонер для волосся",
            "гель для душу", "мило", "зубна паста", "зубна щітка", "ополіскувач", "дезодорант", "крем", "маска для волосся",
            "прокладки", "тампони", "туалетний папір", "серветки", "бритва", "станок для гоління", "піна для гоління"
        ],
        "examples": ["шампунь", "крем", "туалетний папір"],
    },
    {
        "canonical_name": "Побутова хімія",
        "description": "Засоби для прибирання, прання та миття посуду.",
        "aliases": [
            "побутова хімія", "хімія для дому", "мийний засіб", "засіб для прибирання", "порошок", "капсули для прання",
            "пральний порошок", "ополіскувач для білизни", "fairy", "mr proper", "містер пропер", "domestos", "доместос",
            "засіб для миття посуду", "відбілювач", "хлорка", "антижир"
        ],
        "examples": ["Fairy", "капсули для білизни", "Domestos"],
    },
    {
        "canonical_name": "Товари для дому",
        "description": "Посуд, рушники, постіль, меблі, лампочки, дрібний домашній інвентар.",
        "aliases": [
            "товари для дому", "для дому", "посуд", "тарілка", "чашка", "склянка", "рушник", "постіль", "постільна білизна",
            "подушка", "ковдра", "меблі", "стілець", "стіл", "лампочка", "контейнер", "органайзер", "декор", "сушарка для посуду"
        ],
        "examples": ["рушник", "тарілка", "лампочка"],
    },
    {
        "canonical_name": "Канцтовари",
        "description": "Ручки, блокноти, папір, папки, файли, ножиці, скотч та інша офісна дрібнота.",
        "aliases": [
            "канцтовари", "канцтовар", "ручка", "олівець", "блокнот", "зошит", "папір", "папка", "файл", "маркер", "стікери",
            "ножиці", "скотч", "степлер", "діркопробивач"
        ],
        "examples": ["ручка", "блокнот", "папір"],
    },
    {
        "canonical_name": "Одяг",
        "description": "Одяг, взуття, білизна та аксесуари до одягу.",
        "aliases": [
            "одяг", "шмотки", "взуття", "кросівки", "черевики", "футболка", "кофта", "куртка", "джинси", "штани", "шкарпетки",
            "білизна", "ремінь", "шапка", "рукавички"
        ],
        "examples": ["футболка", "кросівки", "джинси"],
    },
    {
        "canonical_name": "Догляд за собою",
        "description": "Стрижка, манікюр, педикюр, салони, косметологія, масаж для себе.",
        "aliases": [
            "догляд за собою", "барбершоп", "стрижка", "перукарня", "манікюр", "педикюр", "брови", "салон", "салон краси",
            "косметолог", "масаж", "spa", "спа"
        ],
        "examples": ["стрижка", "манікюр", "барбершоп"],
    },
    {
        "canonical_name": "Техніка",
        "description": "Ноутбуки, навушники, клавіатури, периферія, комплектуючі ПК та інша електроніка.",
        "aliases": [
            "техніка", "електроніка", "ноутбук", "навушники", "мишка", "клавіатура", "монітор", "ssd", "hdd", "відеокарта",
            "процесор", "оперативна пам'ять", "ram", "блок живлення", "кабель", "зарядка", "периферія", "комплектуючі", "запчастини для пк"
        ],
        "examples": ["ноутбук", "навушники", "клавіатура"],
    },
    {
        "canonical_name": "Пальне",
        "description": "Бензин, дизель, газ та інше паливо для авто.",
        "aliases": [
            "пальне", "бензин", "дизель", "дп", "газ для авто", "lpg", "a95", "a-95", "a92", "a-92", "95 energy", "95 pulls",
            "евро95", "euro95", "upg95", "fuel"
        ],
        "examples": ["A95", "дизель", "LPG"],
    },
    {
        "canonical_name": "Машина",
        "description": "Ремонт авто, СТО, запчастини, автомийка, шиномонтаж, страховка, паркування.",
        "aliases": [
            "машина", "авто", "ремонт машини", "ремонт авто", "сто", "шиномонтаж", "автомийка", "мийка", "запчастини авто",
            "масло авто", "омивач", "парковка", "страховка авто", "техогляд"
        ],
        "examples": ["СТО", "автомийка", "шиномонтаж"],
    },
    {
        "canonical_name": "Транспорт",
        "description": "Таксі, метро, автобуси, маршрутки, потяги та інший транспорт.",
        "aliases": [
            "транспорт", "таксі", "uber", "uklon", "bolt", "метро", "автобус", "маршрутка", "тролейбус", "трамвай", "поїзд", "потяг",
            "електричка", "квиток на транспорт"
        ],
        "examples": ["таксі", "метро", "автобус"],
    },
    {
        "canonical_name": "Розваги",
        "description": "Кіно, театр, концерти, події, художні книги та відпочинок.",
        "aliases": [
            "розваги", "кіно", "театр", "концерт", "квест", "боулінг", "квитки", "подія", "відпочинок", "настільна гра",
            "художня книга", "роман", "комікс"
        ],
        "examples": ["кіно", "театр", "квитки"],
    },
    {
        "canonical_name": "Відеоігри",
        "description": "Ігри, DLC, внутрішньоігрові покупки, Steam, PS Store, Xbox, Epic та GOG.",
        "aliases": [
            "відеоігри", "ігри", "steam", "gog", "epic games", "epic", "ps store", "playstation store", "xbox", "dlc",
            "донат у гру", "внутрішньоігрова покупка", "battle pass"
        ],
        "examples": ["Steam", "DLC", "PS Store"],
    },
    {
        "canonical_name": "Навчання",
        "description": "Курси, конференції, сертифікації, освітні платформи, професійні книги.",
        "aliases": [
            "навчання", "курс", "курси", "конференція", "воркшоп", "лекція", "сертифікація", "сертифікат", "освітня платформа",
            "udemy", "coursera", "навчальна книга", "професійна книга"
        ],
        "examples": ["курс", "конференція", "Udemy"],
    },
    {
        "canonical_name": "Житло та комунальні",
        "description": "Оренда, світло, вода, газ, ОСББ, опалення, квартплата та інші витрати на житло.",
        "aliases": [
            "житло та комунальні", "комунальні", "комуналка", "оренда", "оренда житла", "квартплата", "осбб", "опалення", "газ",
            "світло", "електроенергія", "вода комунальна", "домофон"
        ],
        "examples": ["оренда", "ОСББ", "електроенергія"],
    },
    {
        "canonical_name": "Зв’язок та інтернет",
        "description": "Мобільний зв'язок, домашній інтернет, поповнення номерів та тарифи операторів.",
        "aliases": [
            "зв'язок та інтернет", "зв’язок та інтернет", "мобільний", "поповнення телефону", "домашній інтернет", "інтернет", "провайдер",
            "kyivstar", "київстар", "lifecell", "лайфселл", "vodafone", "водафон"
        ],
        "examples": ["Київстар", "домашній інтернет", "поповнення телефону"],
    },
    {
        "canonical_name": "Цигарки",
        "description": "Сигарети, тютюн, стіки HEETS/TEREA, IQOS, вейпи, рідини, нікотинові товари.",
        "aliases": [
            "цигарки", "сигарети", "тютюн", "табак", "heets", "terea", "iqos", "neo", "veo", "стік", "стіки", "стик", "стики",
            "вейп", "одноразка", "рідина для вейпа", "нікотин"
        ],
        "examples": ["HEETS", "TEREA", "IQOS"],
    },
    {
        "canonical_name": "Подарунки",
        "description": "Подарунки та святкові покупки, коли це явно позначено як подарунок.",
        "aliases": [
            "подарунок", "подарунки", "gift", "це подарунок", "на подарунок", "для подарунка"
        ],
        "examples": ["це подарунок"],
    },
    {
        "canonical_name": "Бізнес / вкладення",
        "description": "Вкладення в бізнес або власні проєкти, коли це явно позначено як вкладення.",
        "aliases": [
            "бізнес", "вклад", "вкладення", "вклад в бізнес", "для бізнесу", "на клініку", "в ветклініку", "передав славі", "бізнес витрата"
        ],
        "examples": ["це вклад", "для бізнесу"],
    },
    {
        "canonical_name": "Підписки та цифрові сервіси",
        "description": "Платні цифрові сервіси та підписки: Netflix, Spotify, ChatGPT, Claude, домени, хостинг.",
        "aliases": [
            "підписки", "підписка", "цифрові сервіси", "netflix", "spotify", "youtube premium", "chatgpt", "claude", "canva",
            "figma", "hosting", "хостинг", "domain", "домен", "ps plus", "game pass"
        ],
        "examples": ["Netflix", "Spotify", "домен"],
    },
    {
        "canonical_name": "Хобі",
        "description": "Шиття, малювання, набори для творчості, тканини, фарби, розмальовки та інші хобі.",
        "aliases": [
            "хобі", "малювання", "фарби", "полотно", "кисті", "шиття", "нитки", "тканина", "набір для творчості", "розмальовка",
            "папір для творчості", "скрапбукінг"
        ],
        "examples": ["фарби", "нитки", "розмальовка"],
    },
    {
        "canonical_name": "Інше",
        "description": "Лише якщо не вдалося надійно класифікувати покупку.",
        "aliases": ["інше", "різне", "misc", "unknown"],
        "examples": ["невідомий товар"],
    },
]


BROAD_CATEGORY_MAP: Dict[str, str] = {
    "продукти": "Продукти",
    "їжа": "Продукти",
    "food": "Продукти",
    "вода": "Вода",
    "мінеральна вода": "Вода",
    "питна вода": "Вода",
    "напої": "Солодкі напої",
    "солодкі напої": "Солодкі напої",
    "сік": "Солодкі напої",
    "соки": "Солодкі напої",
    "енергетики": "Солодкі напої",
    "алкоголь": "Алкоголь",
    "fast food": "Фастфуд і снеки",
    "фастфуд": "Фастфуд і снеки",
    "готова їжа": "Фастфуд і снеки",
    "снеки": "Фастфуд і снеки",
    "солодощі": "Фастфуд і снеки",
    "аптека": "Аптека",
    "ліки": "Аптека",
    "лікарня": "Медичні послуги",
    "медичні послуги": "Медичні послуги",
    "лікар": "Медичні послуги",
    "аналізи": "Медичні послуги",
    "тварини": "Тварини",
    "корм": "Тварини",
    "косметика": "Гігієна та догляд",
    "гігієна": "Гігієна та догляд",
    "догляд": "Гігієна та догляд",
    "побутова хімія": "Побутова хімія",
    "хімія": "Побутова хімія",
    "товари для дому": "Товари для дому",
    "канцтовари": "Канцтовари",
    "одяг": "Одяг",
    "пальне": "Пальне",
    "бензин": "Пальне",
    "дизель": "Пальне",
    "машина": "Машина",
    "транспорт": "Транспорт",
    "розваги": "Розваги",
    "відеоігри": "Відеоігри",
    "ігри": "Відеоігри",
    "навчання": "Навчання",
    "комунальні": "Житло та комунальні",
    "оренда": "Житло та комунальні",
    "житло": "Житло та комунальні",
    "інтернет": "Зв’язок та інтернет",
    "зв'язок": "Зв’язок та інтернет",
    "зв’язок": "Зв’язок та інтернет",
    "цигарки": "Цигарки",
    "сигарети": "Цигарки",
    "тютюн": "Цигарки",
    "подарунок": "Подарунки",
    "подарунки": "Подарунки",
    "вклад": "Бізнес / вкладення",
    "бізнес": "Бізнес / вкладення",
    "підписка": "Підписки та цифрові сервіси",
    "підписки": "Підписки та цифрові сервіси",
    "хобі": "Хобі",
    "інше": "Інше",
}


class CategoryRulesService:
    def __init__(self, file_path: str = "/app/data/category_rules.json") -> None:
        self.file_path = file_path
        self._lock = Lock()
        self._data: Dict[str, Any] = {"rules": [], "meta": {}}
        self._loaded = False

    def _ensure_dir(self) -> None:
        folder = os.path.dirname(self.file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def _normalize(self, text: str) -> str:
        text = str(text or "").lower().strip()
        text = text.replace("ё", "е").replace("’", "'")
        text = re.sub(r"[^a-zа-яіїєґ0-9\s\-]", " ", text)
        text = text.replace("-", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _compact(self, text: str) -> str:
        return self._normalize(text).replace(" ", "")

    def _default_data(self) -> Dict[str, Any]:
        return {"rules": [], "meta": {}}

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._ensure_dir()
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        rules = loaded.get("rules")
                        meta = loaded.get("meta")
                        self._data = {
                            "rules": rules if isinstance(rules, list) else [],
                            "meta": meta if isinstance(meta, dict) else {},
                        }
                    else:
                        self._data = self._default_data()
                except Exception:
                    self._data = self._default_data()
            self._loaded = True

    def _save(self) -> None:
        self._ensure_dir()
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _prepare_aliases(self, canonical_name: str, aliases: List[str]) -> List[str]:
        canonical_name = _pretty_title(canonical_name)
        clean_aliases: List[str] = []
        seen = set()
        for raw in [canonical_name, *aliases]:
            alias = str(raw or "").strip()
            if not alias:
                continue
            norm = self._normalize(alias)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            clean_aliases.append(alias)
        return clean_aliases or [canonical_name]

    def _find_rule_index(self, canonical_name: str) -> Optional[int]:
        normalized_canonical = self._normalize(canonical_name)
        for idx, rule in enumerate(self._data.setdefault("rules", [])):
            if self._normalize(rule.get("canonical_name", "")) == normalized_canonical:
                return idx
        return None

    def _upsert_rule_unlocked(self, canonical_name: str, aliases: List[str]) -> Dict[str, Any]:
        canonical_name = _pretty_title(canonical_name)
        merged_aliases = self._prepare_aliases(canonical_name, aliases)
        idx = self._find_rule_index(canonical_name)
        rules = self._data.setdefault("rules", [])
        if idx is None:
            rule = {"canonical_name": canonical_name, "aliases": merged_aliases}
            rules.append(rule)
            return dict(rule)

        existing = rules[idx]
        existing["canonical_name"] = canonical_name
        merged: List[str] = []
        seen = set()
        for raw in [*existing.get("aliases", []), *merged_aliases]:
            alias = str(raw or "").strip()
            norm = self._normalize(alias)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            merged.append(alias)
        existing["aliases"] = merged
        return dict(existing)

    def ensure_seeded(self) -> None:
        self._load()
        with self._lock:
            meta = self._data.setdefault("meta", {})
            for category in DEFAULT_CATEGORY_CATALOG:
                self._upsert_rule_unlocked(category["canonical_name"], category.get("aliases", []))
            meta["seed_version"] = SEED_VERSION
            meta["canonical_count"] = len(DEFAULT_CATEGORY_CATALOG)
            self._save()

    def list_rules(self) -> List[Dict[str, Any]]:
        self.ensure_seeded()
        return [dict(rule) for rule in self._data.get("rules", [])]

    def list_canonical_categories(self) -> List[str]:
        self.ensure_seeded()
        return [item["canonical_name"] for item in DEFAULT_CATEGORY_CATALOG]

    def get_catalog(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in DEFAULT_CATEGORY_CATALOG]

    def render_receipt_category_guide(self) -> str:
        lines: List[str] = []
        for item in DEFAULT_CATEGORY_CATALOG:
            examples = ", ".join(item.get("examples", [])[:4])
            if examples:
                lines.append(f"- {item['canonical_name']}: {item['description']} Приклади: {examples}.")
            else:
                lines.append(f"- {item['canonical_name']}: {item['description']}")
        return "\n".join(lines)

    def upsert_rule(self, canonical_name: str, aliases: List[str]) -> Dict[str, Any]:
        self.ensure_seeded()
        with self._lock:
            rule = self._upsert_rule_unlocked(canonical_name, aliases)
            self._save()
            return dict(rule)

    def _best_match_score(self, text: str, alias: str) -> float:
        text_norm = self._normalize(text)
        alias_norm = self._normalize(alias)
        if not text_norm or not alias_norm:
            return 0.0
        if alias_norm == text_norm:
            return 1.0
        if alias_norm in text_norm:
            return 0.98
        text_compact = self._compact(text)
        alias_compact = self._compact(alias)
        if alias_compact and alias_compact in text_compact:
            return 0.97
        text_tokens = text_norm.split()
        alias_tokens = alias_norm.split()
        if not text_tokens or not alias_tokens:
            return 0.0
        alias_joined = " ".join(alias_tokens)
        best = 0.0
        if len(alias_tokens) == 1:
            for token in text_tokens:
                best = max(best, SequenceMatcher(None, token, alias_joined).ratio())
        else:
            window = len(alias_tokens)
            for i in range(0, max(len(text_tokens) - window + 1, 1)):
                candidate = " ".join(text_tokens[i:i + window])
                best = max(best, SequenceMatcher(None, candidate, alias_joined).ratio())
        return best

    def resolve_category(self, text: str, fallback: Optional[str] = None) -> Optional[str]:
        self.ensure_seeded()
        best_name = None
        best_score = 0.0
        for rule in self._data.get("rules", []):
            canonical_name = rule.get("canonical_name")
            aliases = rule.get("aliases", [])
            for alias in aliases:
                score = self._best_match_score(text, alias)
                if score > best_score:
                    best_score = score
                    best_name = canonical_name
        if best_name and best_score >= 0.88:
            return best_name
        return fallback

    def resolve_receipt_category(
        self,
        item_name: str,
        model_category: Optional[str] = None,
        merchant: Optional[str] = None,
        fallback: str = "Інше",
    ) -> str:
        self.ensure_seeded()

        item_name = str(item_name or "").strip()
        model_category = str(model_category or "").strip()
        merchant = str(merchant or "").strip()

        candidates = [
            item_name,
            f"{merchant} {item_name}".strip(),
            f"{item_name} {model_category}".strip(),
            f"{merchant} {item_name} {model_category}".strip(),
        ]

        for candidate in candidates:
            if not candidate:
                continue
            matched = self.resolve_category(candidate, fallback=None)
            if matched:
                return matched

        broad = self._normalize(model_category)
        if broad in BROAD_CATEGORY_MAP:
            mapped = BROAD_CATEGORY_MAP[broad]
            if mapped == "Солодкі напої":
                item_match = self.resolve_category(item_name, fallback=None)
                if item_match in {"Вода", "Солодкі напої"}:
                    return item_match
            return mapped

        item_match = self.resolve_category(item_name, fallback=None)
        if item_match:
            return item_match

        return fallback

    def format_rule_result(self, rule: Dict[str, Any]) -> str:
        canonical_name = rule.get("canonical_name", "Нова категорія")
        aliases = rule.get("aliases", [])
        aliases_preview = ", ".join(aliases[:12])
        lines = [
            f"Створив категорію: {canonical_name}",
            "Запам’ятав варіанти для розпізнавання:",
            aliases_preview or canonical_name,
            "",
            "Тепер текстові витрати й позиції з чеків будуть намагатися потрапляти саме в цю категорію.",
        ]
        return "\n".join(lines)


def _pretty_title(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return text
    special_map = {
        "coca cola": "Coca Cola",
        "coca-cola": "Coca Cola",
        "pepsi": "Pepsi",
        "fanta": "Fanta",
        "sprite": "Sprite",
        "monster energy": "Monster Energy",
        "red bull": "Red Bull",
        "iqos": "IQOS",
        "heets": "HEETS",
        "terea": "TEREA",
    }
    low = text.lower()
    if low in special_map:
        return special_map[low]
    return text[:1].upper() + text[1:]
