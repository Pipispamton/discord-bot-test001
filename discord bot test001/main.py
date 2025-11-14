# -*- coding: utf-8 -*-
import discord
from discord.ext import tasks
from discord import app_commands
import logging
import os
import asyncio

from config import CHECK_INTERVAL, SYNC_INTERVAL, API_DELAY
from data_manager import DataManager
from helpers import now_jst

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class RoleBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.data = DataManager()
        self.removal_lock = asyncio.Lock()

    async def setup_hook(self):
        # コマンドツリーをクリア（重複防止）
    #    self.tree.clear_commands(guild=None)
    #    for guild in self.guilds:
    #        self.tree.clear_commands(guild=guild)
        
        await self._sync_commands()

    async def _sync_commands(self):
        try:
            # グローバルコマンド同期
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands globally")
            
            # ギルドコマンド同期
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    logger.info(f"Synced {len(synced)} commands to {guild.name} ({guild.id})")
                except Exception as e:
                    logger.error(f"Failed to sync commands to {guild.name}: {e}")
        except Exception as e:
            logger.error(f"Command sync error: {e}")

bot = RoleBot()

# イベントとコマンドをインポート・登録
from events import setup_events
from commands import setup_commands, setup_command_error_handler

setup_events(bot)
setup_commands(bot)
setup_command_error_handler(bot)

# ...existing code (tasks, on_ready, TOKEN check, etc)...

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_roles():
    try:
        from core import process_role_removal
        total_removed = 0
        for guild in bot.guilds:
            removed = await process_role_removal(bot, guild)
            total_removed += removed
            await asyncio.sleep(API_DELAY)
        await bot.data.save_all()
        if total_removed:
            logger.info(f"Role check completed - Removed: {total_removed}")
    except Exception as e:
        logger.error(f"Role check error: {e}")

@tasks.loop(seconds=SYNC_INTERVAL)
async def sync_data_periodically():
    try:
        from core import sync_data_with_reality
        from helpers import validate_role_data
        from config import DATA_FILE
        for guild in bot.guilds:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    test_data = bot.data._load_json(DATA_FILE, {})
                    if not validate_role_data(test_data):
                        logger.warning(f"定期同期: ロールデータ検証失敗（試行 {attempt + 1}/{max_retries}）")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        else:
                            logger.error(f"定期同期: {guild.name} のデータ読み込みに失敗。このギルドをスキップします。")
                            break
                    
                    await sync_data_with_reality(bot, guild, True)
                    break
                except Exception as e:
                    logger.warning(f"定期同期: ファイル読み込み失敗（試行 {attempt + 1}/{max_retries}）: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        logger.error(f"定期同期: {guild.name} のデータ読み込みが {max_retries} 回失敗。スキップします。")
            
            await asyncio.sleep(1)
        
        await bot.data.save_all()
    except Exception as e:
        logger.error(f"Periodic sync error: {e}")

@check_roles.before_loop
@sync_data_periodically.before_loop
async def wait_until_ready():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} - {len(bot.guilds)} guilds")
    
    # 起動時にコマンドツリーをクリアして再同期
    try:
    #    bot.tree.clear_commands(guild=None)
    #    for guild in bot.guilds:
    #        bot.tree.clear_commands(guild=guild)
    #    logger.info("Command tree cleared")
        
        await asyncio.sleep(1)  # API レート制限対応
        await bot._sync_commands()
    except Exception as e:
        logger.warning(f"Command sync in on_ready failed: {e}")
    
    await bot.wait_until_ready()
    
    from core import sync_data_with_reality, log_message
    for guild in bot.guilds:
        try:
            if not guild.chunked:
                logger.info(f"[{guild.name}] メンバー情報をロード中...")
                await guild.chunk()
            
            await log_message(bot, guild, f"Bot起動完了 ({now_jst().strftime('%Y/%m/%d %H:%M:%S')} JST)", "success")
            await sync_data_with_reality(bot, guild)
        except Exception as e:
            logger.error(f"[{guild.name}] 同期中にエラー: {e}")
    
    await bot.data.save_all()
    
    if not check_roles.is_running():
        check_roles.start()
    if not sync_data_periodically.is_running():
        sync_data_periodically.start()
    
    await asyncio.create_task(check_roles.coro())

TOKEN = os.environ.get("BOT_TOKEN")

if __name__ == "__main__":
    if not TOKEN:
        logger.error("BOT_TOKEN environment variable is not set")
        logger.error("トークンを設定してください:")
        logger.error("  Windows: set BOT_TOKEN=あなたのトークン")
        logger.error("  Mac/Linux: export BOT_TOKEN=あなたのトークン")
        exit(1)
    try:
        logger.info("Starting bot...")
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("Invalid bot token")
        exit(1)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
