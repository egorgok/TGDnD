-- Users table
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username VARCHAR(255) UNIQUE,
    full_name VARCHAR(255),
    active_group_id BIGINT,
    is_premium BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Groups table
CREATE TABLE IF NOT EXISTS groups (
    id BIGINT PRIMARY KEY,
    title VARCHAR(255),
    group_type VARCHAR(50),
    gm_prompt TEXT,
    world_description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Keep existing databases compatible when the init script is run manually.
ALTER TABLE users ADD COLUMN IF NOT EXISTS active_group_id BIGINT;

-- The foreign key is added after both sides exist.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid
        WHERE c.conrelid = 'users'::regclass
          AND c.contype = 'f'
          AND a.attname = 'active_group_id'
          AND a.attnum = ANY(c.conkey)
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT fk_users_active_group
            FOREIGN KEY (active_group_id) REFERENCES groups(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Group members (users in groups)
CREATE TABLE IF NOT EXISTS group_members (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'player',
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Characters table
CREATE TABLE IF NOT EXISTS characters (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    race VARCHAR(100) NOT NULL,
    class VARCHAR(100) NOT NULL,
    level INT DEFAULT 1,
    max_hp INT NOT NULL,
    current_hp INT NOT NULL,
    experience INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Character attributes (STR, DEX, CON, INT, WIS, CHA)
CREATE TABLE IF NOT EXISTS character_attributes (
    id SERIAL PRIMARY KEY,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    strength INT NOT NULL,
    dexterity INT NOT NULL,
    constitution INT NOT NULL,
    intelligence INT NOT NULL,
    wisdom INT NOT NULL,
    charisma INT NOT NULL,
    UNIQUE(character_id)
);

-- Character abilities and passive effects
CREATE TABLE IF NOT EXISTS character_abilities (
    id SERIAL PRIMARY KEY,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    ability_name VARCHAR(255) NOT NULL,
    ability_description TEXT,
    is_racial BOOLEAN DEFAULT FALSE,
    is_class BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Item catalog
CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    item_type VARCHAR(100) NOT NULL,
    rarity VARCHAR(50),
    weight DECIMAL(5, 2),
    description TEXT,
    damage_dice VARCHAR(50),
    damage_type VARCHAR(50),
    armor_class INT,
    is_stackable BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Character inventory
CREATE TABLE IF NOT EXISTS inventory (
    id SERIAL PRIMARY KEY,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    quantity INT DEFAULT 1,
    is_equipped BOOLEAN DEFAULT FALSE,
    slot VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Locations in the world
CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    location_type VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Location connections
CREATE TABLE IF NOT EXISTS location_connections (
    id SERIAL PRIMARY KEY,
    from_location_id INT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    to_location_id INT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    description VARCHAR(255),
    is_bidirectional BOOLEAN DEFAULT TRUE
);

-- NPCs in locations
CREATE TABLE IF NOT EXISTS npcs (
    id SERIAL PRIMARY KEY,
    location_id INT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    personality TEXT,
    role VARCHAR(100),
    is_hostile BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Persistent people and creatures encountered by a player character
CREATE TABLE IF NOT EXISTS game_entities (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100) NOT NULL DEFAULT 'person',
    description TEXT,
    personality TEXT,
    current_hp INT NOT NULL DEFAULT 10,
    max_hp INT NOT NULL DEFAULT 10,
    armor_class INT NOT NULL DEFAULT 10,
    attack_bonus INT NOT NULL DEFAULT 0,
    damage_dice VARCHAR(20) NOT NULL DEFAULT 'd4',
    distance_m INT NOT NULL DEFAULT 5,
    is_hostile BOOLEAN NOT NULL DEFAULT FALSE,
    is_alive BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Campaign-wide NPC identities and factual memories
CREATE TABLE IF NOT EXISTS campaign_npcs (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    aliases TEXT,
    role VARCHAR(255),
    description TEXT,
    personality TEXT,
    current_location VARCHAR(500),
    is_hostile BOOLEAN NOT NULL DEFAULT FALSE,
    is_alive BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_campaign_npcs_group_id ON campaign_npcs(group_id);

CREATE TABLE IF NOT EXISTS npc_memories (
    id SERIAL PRIMARY KEY,
    npc_id INT NOT NULL REFERENCES campaign_npcs(id) ON DELETE CASCADE,
    fact TEXT NOT NULL,
    source VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_npc_memories_npc_id ON npc_memories(npc_id);

-- Game sessions
CREATE TABLE IF NOT EXISTS game_sessions (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    current_location_id INT REFERENCES locations(id),
    is_active BOOLEAN DEFAULT TRUE,
    turn_order TEXT,
    combat_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id)
);

-- Action history
CREATE TABLE IF NOT EXISTS action_history (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    action_description TEXT NOT NULL,
    ai_response TEXT,
    dice_roll INT,
    success BOOLEAN,
    generated_image_url VARCHAR(500),
    consequences TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- D&D Rules cache
CREATE TABLE IF NOT EXISTS d_d_rules (
    id SERIAL PRIMARY KEY,
    rule_category VARCHAR(100) NOT NULL,
    rule_name VARCHAR(255) NOT NULL,
    rule_data JSONB,
    source VARCHAR(255),
    version VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(rule_category, rule_name)
);

-- Character spells
CREATE TABLE IF NOT EXISTS character_spells (
    id SERIAL PRIMARY KEY,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    spell_name VARCHAR(255) NOT NULL,
    spell_level INT NOT NULL,
    is_prepared BOOLEAN DEFAULT FALSE,
    slots_used INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Character currency
CREATE TABLE IF NOT EXISTS character_currency (
    character_id INT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    gold INT NOT NULL DEFAULT 0,
    silver INT NOT NULL DEFAULT 0,
    copper INT NOT NULL DEFAULT 0
);

-- Combat encounters
CREATE TABLE IF NOT EXISTS combat_encounters (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    location_id INT REFERENCES locations(id),
    enemy_name VARCHAR(255),
    enemy_hp INT,
    enemy_ac INT,
    round INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Combat participants
CREATE TABLE IF NOT EXISTS combat_participants (
    id SERIAL PRIMARY KEY,
    encounter_id INT NOT NULL REFERENCES combat_encounters(id) ON DELETE CASCADE,
    character_id INT REFERENCES characters(id) ON DELETE SET NULL,
    initiative INT,
    is_player_character BOOLEAN DEFAULT TRUE,
    turn_order INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Campaign-wide quest offers and one-time completion state
CREATE TABLE IF NOT EXISTS campaign_quests (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    character_id INT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    npc_id INT NOT NULL REFERENCES campaign_npcs(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    reward_xp INT NOT NULL DEFAULT 100,
    reward_gold INT NOT NULL DEFAULT 0,
    reward_silver INT NOT NULL DEFAULT 0,
    reward_copper INT NOT NULL DEFAULT 0,
    reward_items_json TEXT,
    reward_spells_json TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'offered',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_characters_user_id ON characters(user_id);
CREATE INDEX IF NOT EXISTS idx_characters_group_id ON characters(group_id);
CREATE INDEX IF NOT EXISTS idx_inventory_character_id ON inventory(character_id);
CREATE INDEX IF NOT EXISTS idx_locations_group_id ON locations(group_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_group_id ON game_sessions(group_id);
CREATE INDEX IF NOT EXISTS idx_campaign_quests_group_id ON campaign_quests(group_id);
CREATE INDEX IF NOT EXISTS idx_campaign_quests_character_id ON campaign_quests(character_id);
CREATE INDEX IF NOT EXISTS idx_campaign_quests_npc_id ON campaign_quests(npc_id);
CREATE INDEX IF NOT EXISTS idx_action_history_group_id ON action_history(group_id);
CREATE INDEX IF NOT EXISTS idx_action_history_character_id ON action_history(character_id);
CREATE INDEX IF NOT EXISTS idx_group_members_group_id ON group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_group_members_user_id ON group_members(user_id);
