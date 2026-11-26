"""
D&D Constants and Configuration
"""

# D&D Difficulty Classes
DC = {
    "VERY_EASY": 5,
    "EASY": 10,
    "MEDIUM": 15,
    "HARD": 20,
    "VERY_HARD": 25,
    "NEARLY_IMPOSSIBLE": 30,
}

# Experience thresholds by level
EXPERIENCE_THRESHOLDS = {
    1: 0,
    2: 300,
    3: 900,
    4: 2700,
    5: 6500,
    6: 14000,
    7: 23000,
    8: 34000,
    9: 48000,
    10: 85000,
    11: 100000,
    12: 120000,
    13: 140000,
    14: 165000,
    15: 195000,
    16: 225000,
    17: 265000,
    18: 305000,
    19: 355000,
    20: 999999,
}

# Damage types
DAMAGE_TYPES = [
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
]

# Armor types
ARMOR_TYPES = {
    "light": ["leather", "studded_leather"],
    "medium": ["hide", "chain_shirt", "scale_mail", "breastplate", "half_plate"],
    "heavy": ["ring_mail", "chain_mail", "plate"],
}

# Weapon types
WEAPON_TYPES = {
    "simple_melee": [
        "club", "dagger", "greatclub", "handaxe", "javelin", "light_hammer", "mace", "quarterstaff", "sickle", "spear"
    ],
    "simple_ranged": [
        "dart", "hand_crossbow", "light_crossbow", "sling"
    ],
    "martial_melee": [
        "battleaxe", "flail", "glaive", "greataxe", "greatsword", "halberd", "lance", "longsword", "maul", "morningstar", "pike", "rapier", "scimitar", "shortsword", "trident", "war_pick", "warhammer", "whip"
    ],
    "martial_ranged": [
        "blowgun", "crossbow_hand", "crossbow_light", "crossbow_heavy", "longbow", "net"
    ],
}

# Status effects
STATUS_EFFECTS = {
    "blinded": {"duration": "variable", "disadvantage": True},
    "charmed": {"duration": "variable", "effect": "disadvantage on attack rolls against charmer"},
    "deafened": {"duration": "variable", "effect": "unable to hear"},
    "exhaustion": {"duration": "until rest", "effect": "movement halved, disadvantage on checks"},
    "frightened": {"duration": "variable", "disadvantage": True},
    "grappled": {"duration": "until grapple ends", "effect": "movement = 0"},
    "invisible": {"duration": "variable", "advantage": True},
    "paralyzed": {"duration": "variable", "effect": "can't move or speak"},
    "petrified": {"duration": "variable", "effect": "can't move or speak"},
    "poisoned": {"duration": "variable", "disadvantage": True},
    "prone": {"duration": "until stand up", "disadvantage": True},
    "restrained": {"duration": "variable", "disadvantage": True},
    "stunned": {"duration": "variable", "effect": "can't move or speak"},
    "unconscious": {"duration": "variable", "effect": "can't move or speak, unaware"},
}

# Spellcasting ability by class
SPELLCASTING_BY_CLASS = {
    "Bard": "charisma",
    "Cleric": "wisdom",
    "Druid": "wisdom",
    "Paladin": "charisma",
    "Ranger": "wisdom",
    "Sorcerer": "charisma",
    "Warlock": "charisma",
    "Wizard": "intelligence",
}

# Skill to ability mapping
SKILLS_TO_ABILITY = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}
