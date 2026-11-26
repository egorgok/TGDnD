"""Russian display names for SRD starter content; source records stay canonical."""

ITEM_NAMES_RU = {
    "Greataxe": "Большой топор",
    "Handaxe": "Ручной топор",
    "Javelin": "Метательное копьё",
    "Explorer's Pack": "Набор путешественника",
    "Rapier": "Рапира",
    "Diplomat's Pack": "Набор дипломата",
    "Lute": "Лютня",
    "Leather Armor": "Кожаный доспех",
    "Dagger": "Кинжал",
    "Mace": "Булава",
    "Scale mail": "Чешуйчатый доспех",
    "Crossbow, light": "Лёгкий арбалет",
    "Crossbow bolt": "Арбалетный болт",
    "Crossbow, hand": "Ручной арбалет",
    "Crossbow, heavy": "Тяжёлый арбалет",
    "Priest's Pack": "Набор священника",
    "Shield": "Щит",
    "Holy Symbol": "Священный символ",
    "Scimitar": "Скимитар",
    "Druidic Focus": "Друидический фокус",
    "Chain mail": "Кольчуга",
    "Longsword": "Длинный меч",
    "Dungeoneer's Pack": "Набор исследователя подземелий",
    "Shortsword": "Короткий меч",
    "Dart": "Дротик",
    "Longbow": "Длинный лук",
    "Quiver": "Колчан",
    "Shortbow": "Короткий лук",
    "Burglar's Pack": "Набор взломщика",
    "Thieves' tools": "Инструменты вора",
    "Component Pouch": "Сумка компонентов",
    "Scholar's Pack": "Набор учёного",
    "Quarterstaff": "Боевой посох",
    "Spellbook": "Книга заклинаний",
    "Arrow (bow)": "Стрела",
    "Warhammer": "Боевой молот",
    "Leather": "Кожа",
    "Club": "Дубинка",
    "Sling": "Праща",
    "Spear": "Копьё",
    "Battleaxe": "Боевой топор",
    "Hammer": "Молот",
    "Staff": "Посох",
    "Pike": "Пика",
    "Trident": "Трезубец",
}
ITEM_NAMES_EN_BY_RU = {
    localized.casefold(): english
    for english, localized in ITEM_NAMES_RU.items()
}
ITEM_NAME_ALIASES_EN_BY_RU = {
    "лёгкий арбалет": "Crossbow, light",
    "легкий арбалет": "Crossbow, light",
    "тяжёлый арбалет": "Crossbow, heavy",
    "тяжелый арбалет": "Crossbow, heavy",
    "ручной арбалет": "Crossbow, hand",
}

SPELL_NAMES_RU = {
    "Acid Splash": "Кислотный всплеск",
    "Alarm": "Тревога",
    "Animal Friendship": "Дружба с животными",
    "Bane": "Порча",
    "Bless": "Благословение",
    "Burning Hands": "Горящие руки",
    "Charm Person": "Очарование личности",
    "Chill Touch": "Леденящее прикосновение",
    "Color Spray": "Цветная пыль",
    "Comprehend Languages": "Понимание языков",
    "Dancing Lights": "Танцующие огоньки",
    "Detect Magic": "Обнаружение магии",
    "Druidcraft": "Искусство друидов",
    "Eldritch Blast": "Потусторонний разряд",
    "Fire Bolt": "Огненный снаряд",
    "Guidance": "Наставление",
    "Light": "Свет",
    "Mending": "Починка",
}

FEATURE_NAMES_RU = {
    "Rage": "Ярость",
    "Unarmored Defense": "Защита без доспехов",
    "Bardic Inspiration": "Вдохновение барда",
    "Spellcasting": "Использование заклинаний",
    "Divine Domain": "Божественный домен",
    "Druidic": "Друидический язык",
    "Fighting Style": "Боевой стиль",
    "Second Wind": "Второе дыхание",
    "Martial Arts": "Боевые искусства",
    "Divine Sense": "Божественное чувство",
    "Lay on Hands": "Возложение рук",
    "Favored Enemy": "Избранный враг",
    "Natural Explorer": "Природный исследователь",
    "Expertise": "Компетентность",
    "Sneak Attack": "Скрытая атака",
    "Thieves' Cant": "Воровской жаргон",
    "Sorcerous Origin": "Происхождение чародея",
    "Arcane Recovery": "Магическое восстановление",
    "Otherworldly Patron": "Потусторонний покровитель",
    "Pact Magic": "Магия договора",
}

FEATURE_DESCRIPTIONS_RU = {
    "Rage": "Бонусным действием впадает в ярость: получает преимущество на проверки и спасброски Силы, бонус к урону сильными атаками и сопротивление дробящему, колющему и рубящему урону.",
    "Unarmored Defense": "Варвар: без доспеха КД равен 10 + модификатор Ловкости + модификатор Телосложения. Монах: 10 + Ловкость + Мудрость, если не носит доспех и не использует щит.",
    "Bardic Inspiration": "Бонусным действием вдохновляет существо в пределах 60 футов. В течение 10 минут оно может добавить к одной проверке, атаке или спасброску кость вдохновения d6.",
    "Spellcasting": "Персонаж знает и использует заклинания своего класса; конкретные заклинания указаны в листе персонажа.",
    "Divine Domain": "Выбирает божественный домен, определяющий дополнительные особенности жреца.",
    "Druidic": "Знает тайный друидический язык и может оставлять на нём скрытые послания.",
    "Fighting Style": "Выбирает специализацию боевого стиля; её эффект зависит от выбранного варианта.",
    "Second Wind": "Бонусным действием восстанавливает 1d10 + уровень бойца HP. Использование восстанавливается после короткого или продолжительного отдыха.",
    "Martial Arts": "Использует Ловкость вместо Силы для безоружных атак и атак монашеским оружием; безоружный удар наносит 1d4 урона.",
    "Divine Sense": "Определяет присутствие небожителей, исчадий и нежити поблизости, если они не находятся за полным укрытием.",
    "Lay on Hands": "Имеет запас лечения в 5 HP за уровень паладина; может восстановить HP касанием или потратить 5 HP запаса, чтобы снять яд или болезнь.",
    "Favored Enemy": "Получает преимущества при выслеживании избранного типа врагов и при вспоминании сведений о них.",
    "Natural Explorer": "Получает преимущества в путешествиях и выживании на выбранной местности.",
    "Expertise": "Удваивает бонус мастерства для двух выбранных навыков или инструментов.",
    "Sneak Attack": "Раз в ход наносит дополнительный 1d6 урона при попадании оружием со свойством «фехтовальное» или дальнобойным оружием, если выполнены условия скрытой атаки.",
    "Thieves' Cant": "Знает тайный жаргон воров, позволяющий передавать скрытые сообщения.",
    "Sorcerous Origin": "Выбирает происхождение магии чародея, которое определяет дальнейшие особенности.",
    "Arcane Recovery": "Один раз после короткого отдыха восстанавливает ячейки заклинаний суммарного уровня до половины уровня волшебника, округляя вверх.",
    "Otherworldly Patron": "Заключает договор с потусторонним покровителем, который определяет особенности колдуна.",
    "Pact Magic": "Использует магию договора: знает заклинания колдуна и восстанавливает ячейки после короткого или продолжительного отдыха.",
}

FEATURE_NAMES_EN_BY_RU = {localized: english for english, localized in FEATURE_NAMES_RU.items()}
SPELL_NAMES_EN_BY_RU = {localized.casefold(): english for english, localized in SPELL_NAMES_RU.items()}


def localized_item_name(english_name: str) -> str:
    return ITEM_NAMES_RU.get(english_name, english_name)


def canonical_item_name(display_name: str) -> str:
    normalized = display_name.casefold()
    return ITEM_NAME_ALIASES_EN_BY_RU.get(
        normalized,
        ITEM_NAMES_EN_BY_RU.get(normalized, display_name)
    )


def localized_item_type(item_type: str) -> str:
    return {
        "gear": "снаряжение",
        "shield": "щит",
        "weapon:melee": "оружие ближнего боя",
        "weapon:ranged": "дальнобойное оружие",
        "weapon:improvised": "подручное оружие",
        "armor:light": "лёгкий доспех",
        "armor:medium": "средний доспех",
        "armor:heavy": "тяжёлый доспех",
    }.get(item_type, "снаряжение")


def localized_spell_name(english_name: str) -> str:
    return SPELL_NAMES_RU.get(english_name, english_name)


def localized_feature_name(english_name: str) -> str:
    return FEATURE_NAMES_RU.get(english_name, english_name)


def localized_feature_description(english_or_russian_name: str, fallback: str = "") -> str:
    english_name = FEATURE_NAMES_EN_BY_RU.get(english_or_russian_name, english_or_russian_name)
    return FEATURE_DESCRIPTIONS_RU.get(english_name, fallback)


def canonical_spell_name(display_name: str) -> str:
    return SPELL_NAMES_EN_BY_RU.get(display_name.casefold(), display_name)
