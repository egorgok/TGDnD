import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
        self.bot = Bot(
            token=self.settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()

    async def init_db(self):
        """Initialize database tables"""
        try:
            Base.metadata.create_all(engine)
            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    async def on_startup(self):
        """Run on bot startup"""
        logger.info("Bot starting up...")
        await self.init_db()
        logger.info("Bot ready!")

    async def on_shutdown(self):
        """Run on bot shutdown"""
        logger.info("Bot shutting down...")
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
        self.dp.include_router(start.router)
        self.dp.include_router(character.router)
        self.dp.include_router(profile.router)
        self.dp.include_router(game.router)
        self.dp.include_router(group.router)

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
