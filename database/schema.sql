-- Users table
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username VARCHAR(255) UNIQUE,
    full_name VARCHAR(255),
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

-- Group members (users in groups)
CREATE TABLE IF NOT EXISTS group_members (
    id SERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'player',
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, user_id)
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, group_id)
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(character_id, item_id)
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
    is_bidirectional BOOLEAN DEFAULT TRUE,
    UNIQUE(from_location_id, to_location_id)
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

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_characters_user_id ON characters(user_id);
CREATE INDEX IF NOT EXISTS idx_characters_group_id ON characters(group_id);
CREATE INDEX IF NOT EXISTS idx_inventory_character_id ON inventory(character_id);
CREATE INDEX IF NOT EXISTS idx_locations_group_id ON locations(group_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_group_id ON game_sessions(group_id);
CREATE INDEX IF NOT EXISTS idx_action_history_group_id ON action_history(group_id);
CREATE INDEX IF NOT EXISTS idx_action_history_character_id ON action_history(character_id);
CREATE INDEX IF NOT EXISTS idx_group_members_group_id ON group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_group_members_user_id ON group_members(user_id);
