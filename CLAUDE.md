# CLAUDE.md - D&D RPG Telegram Bot Documentation

## Project Overview

D&D RPG Bot - это асинхронный Telegram-бот для ролевых игр в стиле Dungeons & Dragons 5e.

**Tech Stack:**
- Backend: Python 3.10+ + aiogram 3.4
- Database: PostgreSQL 15
- Cache: Redis 7
- ORM: SQLAlchemy 2.0
- APIs: Custom gateways для GPT-6-Luna (текст) и GPT-Image-2 (изображения)

## Architecture

```
src/
├── bot.py                  # Главная точка входа, инициализация бота
├── models/                 # SQLAlchemy модели (11 таблиц)
│   ├── base.py            # Base и SessionLocal
│   ├── user.py            # User
│   ├── group.py           # Group, GroupMember
│   ├── character.py       # Character, CharacterAttribute, CharacterAbility, CharacterSpell
│   ├── item.py            # Item, Inventory
│   ├── location.py        # Location, LocationConnection, NPC
│   ├── session.py         # GameSession
│   ├── action.py          # ActionHistory
│   ├── rules.py           # DnDRules
│   └── combat.py          # CombatEncounter, CombatParticipant
├── services/              # Бизнес-логика
│   ├── ai_service.py      # Интеграция с ИИ гейтвеями
│   ├── game_engine_service.py  # Механика боя, d20, урон, XP
│   ├── character_service.py    # Создание, левелинг, управление персонажами
│   ├── inventory_service.py    # Инвентарь, экипировка
│   └── rules_service.py        # Валидация правил D&D
├── handlers/              # Обработчики команд Telegram
│   ├── start.py          # /start, основное меню
│   ├── character.py      # Создание персонажей
│   ├── profile.py        # Профиль персонажа
│   ├── game.py           # Действия в игре
│   └── group.py          # Групповые функции
├── constants/            # Константы D&D
└── utils/               # Утилиты форматирования

config/
└── settings.py          # Pydantic settings из .env

database/
├── schema.sql           # Полная SQL схема БД
└── migrations/          # Alembic миграции (если используются)
```

## Database Schema

### Core Tables

**users** - Telegram пользователи
```
id (BigInt, PK), username, full_name, is_premium, created_at, updated_at
```

**groups** - Группы/кампании
```
id (BigInt, PK), title, group_type, gm_prompt (пользовательский сеттинг), 
world_description, created_at, updated_at
```

**group_members** - Участники группы
```
id, group_id (FK), user_id (FK), role, joined_at
```

**characters** - Персонажи игроков
```
id, user_id (FK), group_id (FK), name, race, class, level, max_hp, current_hp,
experience, created_at, updated_at
```

**character_attributes** - Характеристики (STR, DEX, CON, INT, WIS, CHA)
```
id, character_id (FK, UNIQUE), strength, dexterity, constitution, 
intelligence, wisdom, charisma
```

**character_abilities** - Способности и баффы
```
id, character_id (FK), ability_name, ability_description, is_racial, 
is_class, created_at
```

**items** - Каталог предметов
```
id, name (UNIQUE), item_type, rarity, weight, description, damage_dice,
damage_type, armor_class, is_stackable, created_at
```

**inventory** - Инвентарь персонажей
```
id, character_id (FK), item_id (FK), quantity, is_equipped, slot, created_at
```

**locations** - Локации в мире
```
id, group_id (FK), name, description, location_type, created_at
```

**location_connections** - Связи между локациями
```
id, from_location_id (FK), to_location_id (FK), description, is_bidirectional
```

**npcs** - NPC в локациях
```
id, location_id (FK), name, personality, role, is_hostile, created_at
```

**game_sessions** - Текущие игровые сессии
```
id, group_id (FK, UNIQUE), current_location_id (FK), is_active, turn_order,
combat_active, created_at, updated_at
```

**action_history** - История всех действий
```
id, group_id (FK), character_id (FK), action_description, ai_response,
dice_roll, success, generated_image_url, consequences, created_at
```

**d_d_rules** - Кэш правил D&D 5e
```
id, rule_category, rule_name, rule_data (JSONB), source, version, updated_at
```

**character_spells** - Заклинания персонажа
```
id, character_id (FK), spell_name, spell_level, is_prepared, slots_used, created_at
```

**combat_encounters** - Боевые встречи
```
id, session_id (FK), location_id (FK), enemy_name, enemy_hp, enemy_ac,
round, is_active, created_at, updated_at
```

**combat_participants** - Участники боя
```
id, encounter_id (FK), character_id (FK), initiative, is_player_character,
turn_order, created_at
```

## API Integration

### Text Generation (GPT-6-Luna)
**URL:** `https://ru.cheapvibecode.ru/v1/chat/completions`

Запрос:
```json
{
  "model": "gpt-6-luna",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "max_tokens": 500,
  "temperature": 0.7
}
```

Заголовок авторизации:
```
Authorization: Bearer sk-cvc-be1c896698602d88423cb30fc908660c857f4d5279082ad2d29479ba51cc48d8
```

### Image Generation (GPT-Image-2)
**URL:** `https://cheapvibecode.ru/v1/images/generations`

Запрос:
```json
{
  "model": "gpt-image-2",
  "prompt": "...",
  "size": "auto",
  "quality": "low",
  "n": 1,
  "response_format": "b64_json"
}
```

Заголовок авторизации:
```
Authorization: Bearer sk-cvc-2dfe5551b9a8b95513d383e7e79176364424a2b033e1b37dcba21141ee7416a2
```

## Game Mechanics

### Action Resolution
1. Игрок вводит `/action [описание]`
2. Система извлекает данные персонажа (инвентарь, HP, способности, раса, класс)
3. Строится промпт для ИИ с контекстом мира
4. ИИ валидирует возможность действия
5. Проводится проверка d20 (1-20)
6. Применяются последствия (урон, потеря предметов, изменение локации, БД)
7. При необходимости генерируется изображение локации/боя
8. Логируется в action_history

### Dice System
- **d20 check:** Основная проверка действий
- **Advantage/Disadvantage:** Бросить d20 дважды и взять выше/ниже
- **Modifiers:** Добавляются из характеристик и способностей

### Combat
- **Initiative:** d20 + DEX modifier
- **Attack Roll:** d20 + attack bonus vs target AC
- **Damage:** weapon die + modifiers
- **Multi-round:** Система очереди ходов

### Character Progression
- **Leveling:** XP thresholds по таблице D&D
- **HP:** Увеличение при левелинге (1 + CON modifier, минимум 1)
- **Proficiency Bonus:** Автоматически растёт с уровнем

## Development Setup

### Local Development with Docker
```bash
docker-compose up -d
```

### Manual Setup
```bash
# 1. Create venv
python -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env from example
cp .env.example .env
# Edit .env with your tokens

# 4. Initialize DB
psql -U postgres -f database/schema.sql

# 5. Run bot
python main.py
```

## Key Principles

1. **Async-first:** Вся коммуникация с Telegram и ИИ асинхронна
2. **State per group:** Каждая группа имеет свой сеттинг и мир
3. **Character isolation:** Персонажи в группе независимы, но действия влияют на мир
4. **D&D 5e compliance:** Правила валидируются по D&D 5e (из dnd.su)
5. **Flexible actions:** Игроки могут делать ЧТО УГОДНО, ИИ-ГМ оценивает возможность
6. **Dice resolution:** Все проверяемые действия должны иметь d20 бросок

## Future Enhancements

- [ ] Парсер dnd.su для загрузки актуальных правил
- [ ] Multi-party support (несколько партий в одной группе)
- [ ] Reputation/faction system
- [ ] Loot tables для генерации сокровищ
- [ ] Advanced combat AI (тактическое поведение врагов)
- [ ] Voice message support для речи персонажей
- [ ] Save/load game state между сессиями
