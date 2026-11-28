import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    BotCommand, BotCommandScopeAllGroupChats
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from config.settings import get_settings
from src.models.base import engine, Base

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DnDBot:
    def __init__(self):
        self.settings = get_settings()
        self.storage = MemoryStorage()
        self.bot = Bot(
            token=self.settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher(storage=self.storage)

    async def init_db(self):
        """Initialize database tables"""
        try:
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                for column, col_type in (
                    ("reward_gold", "INTEGER NOT NULL DEFAULT 0"),
                    ("reward_silver", "INTEGER NOT NULL DEFAULT 0"),
                    ("reward_copper", "INTEGER NOT NULL DEFAULT 0"),
                    ("reward_items_json", "TEXT"),
                    ("reward_spells_json", "TEXT"),
                ):
                    conn.exec_driver_sql(
                        f"ALTER TABLE campaign_quests ADD COLUMN IF NOT EXISTS {column} {col_type}"
                    )
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS active_group_id BIGINT"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE game_entities ADD COLUMN IF NOT EXISTS ability_scores JSONB"
                )
                for column, col_type in (
                    ("background", "VARCHAR(100)"),
                    ("death_successes", "INTEGER NOT NULL DEFAULT 0"),
                    ("death_failures", "INTEGER NOT NULL DEFAULT 0"),
                    ("lay_on_hands_remaining", "INTEGER"),
                    ("is_dead", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ):
                    conn.exec_driver_sql(
                        f"ALTER TABLE characters ADD COLUMN IF NOT EXISTS {column} {col_type}"
                    )
                conn.exec_driver_sql(
                    "ALTER TABLE campaign_quests ALTER COLUMN character_id DROP NOT NULL"
                )
                conn.exec_driver_sql(
                    """
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
                            FOREIGN KEY (active_group_id)
                            REFERENCES groups(id) ON DELETE SET NULL;
                        END IF;
                    END $$;
                    """
                )
            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

        # The 2024 SRD snapshot is bundled locally from 5e-bits and is the
        # sole rules edition used by game mechanics.
        try:
            from src.models.base import SessionLocal
            from src.services.srd2024_importer import sync_srd2024

            srd_db = SessionLocal()
            try:
                counts = await asyncio.to_thread(sync_srd2024, srd_db)
                logger.info("Bundled 2024 SRD data loaded: %s", counts)
            finally:
                srd_db.close()
        except Exception:
            logger.exception("Could not load bundled 2024 SRD; continuing with existing rules")

    async def on_startup(self):
        """Run on bot startup"""
        logger.info("Bot starting up...")
        await self.init_db()
        group_commands = [
            BotCommand(command="action", description="Совершить действие персонажа"),
            BotCommand(command="profile", description="Показать профиль персонажа"),
            BotCommand(command="inspect", description="Осмотреть знакомого NPC"),
            BotCommand(command="location", description="Показать текущую локацию"),
            BotCommand(command="travel", description="Путешествовать"),
            BotCommand(command="inventory", description="Показать инвентарь"),
            BotCommand(command="send", description="Передать монеты союзнику"),
            BotCommand(command="deathsave", description="Спасбросок за павшего союзника"),
            BotCommand(command="help", description="Помощь"),
        ]
        try:
            await self.bot.set_my_commands(
                group_commands,
                scope=BotCommandScopeAllGroupChats()
            )
            logger.info("Group chat commands registered")
        except Exception:
            logger.exception("Could not register group chat command menu")
        logger.info("Bot ready!")

    async def on_shutdown(self):
        """Run on bot shutdown"""
        logger.info("Bot shutting down...")
        await self.storage.close()
        await self.bot.session.close()

    def register_handlers(self):
        """Register all command handlers"""
        from src.handlers import (
            start,
            character,
            profile,
            game,
            group
        )

        # Register routers
        self.dp.include_router(character.router)
        self.dp.include_router(game.router)
        self.dp.include_router(profile.router)
        self.dp.include_router(group.router)
        self.dp.include_router(start.router)

        logger.info("Handlers registered")

    async def run(self):
        """Start the bot"""
        try:
            self.register_handlers()
            await self.on_startup()
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        finally:
            await self.on_shutdown()

async def main():
    """Main entry point"""
    bot = DnDBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
