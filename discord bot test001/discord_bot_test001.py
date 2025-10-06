# -*- coding: utf-8 -*-
import discord
from discord.ext import tasks
from discord import app_commands
import json
import os
import shutil
import asyncio
import logging
import functools
from datetime import datetime, timezone, timedelta
from discord.ui import Modal, TextInput, View, Button

DEBUG = True

DATA_FILE = "roles_data.json"
SETTINGS_FILE = "bot_settings_debug.json" if DEBUG else "bot_settings.json"
ROLE_HISTORY_FILE = "role_add_history.json"
LOG_CHANNEL_FILE = "log_channel_settings.json"
BACKUP_DIR = "backup"

JST = timezone(timedelta(hours=9))
now_jst = lambda: datetime.now(JST)
timestamp_to_jst = lambda ts: datetime.fromtimestamp(ts, JST)

CHECK_INTERVAL = 10 if DEBUG else 600
SYNC_INTERVAL = 15 if DEBUG else 3600
BATCH_SIZE = 20 if DEBUG else 50
API_DELAY = 0.5 if DEBUG else 0.2
ROLES_TO_AUTO_REMOVE = ["注意", "警告"]
DEFAULT_REMOVE_SECONDS = {r: (15 if DEBUG else 90 * 86400) for r in ROLES_TO_AUTO_REMOVE}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒" if seconds % 60 else f"{seconds // 60}分"
    elif seconds < 86400:
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{seconds // 3600}時間{m}分{s}秒" if m or s else f"{seconds // 3600}時間"
    else:
        h = (seconds % 86400) // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        val = f"{seconds // 86400}日"
        if h or m or s:
            if h: val += f"{h}時間"
            if m: val += f"{m}分"
            if s: val += f"{s}秒"
        return val

def parse_duration(days=0, hours=0, minutes=0, seconds=0):
    return int(days) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)

class DataManager:
    def __init__(self):
        self.role_data = {}
        self.settings = {}
        self.role_add_history = {}
        self.guild_log_channels = {}
        self._lock = asyncio.Lock()
        self.load_all()

    def load_all(self):
        self.role_data = self._load_json(DATA_FILE, {})
        self.settings = self._load_json(SETTINGS_FILE, {"remove_seconds": DEFAULT_REMOVE_SECONDS.copy()})
        self.role_add_history = self._load_json(ROLE_HISTORY_FILE, {})
        self.guild_log_channels = self._load_json(LOG_CHANNEL_FILE, {})
        # 履歴変換
        for g, users in self.role_add_history.items():
            for u, roles in users.items():
                for r, hist in roles.items():
                    if hist and isinstance(hist[0], float):
                        self.role_add_history[g][u][r] = [{"timestamp": ts, "reason": ""} for ts in hist]
        self.settings.setdefault("remove_seconds", DEFAULT_REMOVE_SECONDS.copy())
        for r in ROLES_TO_AUTO_REMOVE:
            self.settings["remove_seconds"].setdefault(r, DEFAULT_REMOVE_SECONDS[r])

    def _load_json(self, file_path, default):
        if not os.path.exists(file_path):
            self._save_json(file_path, default)
            return default
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}")
            return default

    def _save_json(self, file_path, data):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving {file_path}: {e}")

    async def save_all(self):
        async with self._lock:
            changed = False
            # 変更検知: 保存前後でデータが変わった場合のみバックアップ
            old_data = self._load_json(DATA_FILE, {})
            old_settings = self._load_json(SETTINGS_FILE, {})
            old_history = self._load_json(ROLE_HISTORY_FILE, {})
            old_log = self._load_json(LOG_CHANNEL_FILE, {})
            if old_data != self.role_data or old_settings != self.settings or old_history != self.role_add_history or old_log != self.guild_log_channels:
                self._backup_data()
                changed = True
            self._save_json(DATA_FILE, self.role_data)
            self._save_json(SETTINGS_FILE, self.settings)
            self._save_json(ROLE_HISTORY_FILE, self.role_add_history)
            self._save_json(LOG_CHANNEL_FILE, self.guild_log_channels)

    def _backup_data(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = now_jst().strftime("%Y%m%d_%H%M%S")
        backup_targets = [
            (DATA_FILE, f"roles_data_{ts}.json"),
            (SETTINGS_FILE, f"settings_{ts}.json"),
            (ROLE_HISTORY_FILE, f"role_history_{ts}.json"),
            (LOG_CHANNEL_FILE, f"log_channel_{ts}.json"),
        ]
        for src, dst in backup_targets:
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(BACKUP_DIR, dst))
        self._cleanup_old_backups()

    def _cleanup_old_backups(self):
        try:
            # roles_data, settings, role_history, log_channel それぞれ10世代まで残す
            patterns = [
                "roles_data_",
                "settings_",
                "role_history_",
                "log_channel_",
            ]
            for pat in patterns:
                backups = sorted(
                    [f for f in os.listdir(BACKUP_DIR) if f.startswith(pat)],
                    reverse=True
                )
                for old_backup in backups[10:]:
                    os.remove(os.path.join(BACKUP_DIR, old_backup))
        except Exception as e:
            logger.error(f"Backup cleanup error: {e}")

    def get_remove_seconds(self, guild_id, user_id, role_name):
        user_setting = (
            self.settings.get("user_remove_seconds", {}).get(guild_id, {})
            .get(user_id, {}).get(role_name)
        )
        if user_setting is not None:
            return user_setting
        return self.settings["remove_seconds"].get(role_name, DEFAULT_REMOVE_SECONDS.get(role_name, 90 * 86400))

    def set_user_remove_seconds(self, guild_id, user_id, role_name, seconds):
        self.settings.setdefault("user_remove_seconds", {}).setdefault(guild_id, {}).setdefault(user_id, {})[role_name] = seconds

    def remove_user_setting(self, guild_id, user_id, role_name):
        try:
            user_roles = self.settings.get("user_remove_seconds", {}).get(guild_id, {}).get(user_id, {})
            if role_name in user_roles:
                del user_roles[role_name]
                if not user_roles:
                    del self.settings["user_remove_seconds"][guild_id][user_id]
                if not self.settings["user_remove_seconds"][guild_id]:
                    del self.settings["user_remove_seconds"][guild_id]
                if not self.settings["user_remove_seconds"]:
                    del self.settings["user_remove_seconds"]
                return True
        except KeyError:
            pass
        return False

    def add_role_history(self, guild_id, user_id, role_name, timestamp):
        if role_name not in ROLES_TO_AUTO_REMOVE:
            return
        self.role_add_history.setdefault(guild_id, {}).setdefault(user_id, {}).setdefault(role_name, []).append({
            "timestamp": timestamp,
            "reason": ""
        })

    def edit_role_history_reason(self, guild_id, user_id, role_name, index, reason):
        try:
            self.role_add_history[guild_id][user_id][role_name][index]["reason"] = reason
            return True
        except Exception:
            return False

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
        await self._sync_commands()

    async def _sync_commands(self):
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands globally")
            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(f"Synced {len(synced)} commands to {guild.name}")
        except Exception as e:
            logger.error(f"Command sync error: {e}")

bot = RoleBot()

async def log_message(guild, message, level="info"):
    channel_id = bot.data.guild_log_channels.get(str(guild.id))
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
    try:
        if channel:
            emoji = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}.get(level, "📝")
            await channel.send(f"{emoji} {message}"[:2000])
    except Exception as e:
        logger.error(f"Discord log error: {e}")
    getattr(logger, level if level != "success" else "info")(f"[{guild.name}] {message}")

async def add_role_with_timestamp(member, role, reason=None):
    try:
        guild_id, user_id = str(member.guild.id), str(member.id)
        bot.data.role_data.setdefault(guild_id, {}).setdefault(user_id, {})
        if role in member.roles:
            return True
        now_ts = now_jst().timestamp()
        # --- ロール新規付与時は個人削除期間設定をリセット ---
        if role.name in ROLES_TO_AUTO_REMOVE:
            bot.data.remove_user_setting(guild_id, user_id, role.name)
        if role.name in ROLES_TO_AUTO_REMOVE and role.name not in bot.data.role_data[guild_id][user_id]:
            bot.data.role_data[guild_id][user_id][role.name] = now_ts
            bot.data.add_role_history(guild_id, user_id, role.name, now_ts)
        elif role.name not in bot.data.role_data[guild_id][user_id]:
            bot.data.role_data[guild_id][user_id][role.name] = now_ts
        await member.add_roles(role, reason=reason or "自動ロール付与")
        await bot.data.save_all()
        return True
    except Exception as e:
        logger.error(f"Role add error for {member}: {e}")
        return False

async def sync_data_with_reality(guild, is_periodic=False):
    try:
        now = now_jst().timestamp()
        guild_id = str(guild.id)
        bot.data.role_data.setdefault(guild_id, {})
        current_holders = {}
        # set型で高速化
        auto_roles_set = set(ROLES_TO_AUTO_REMOVE)
        for member in guild.members:
            if member.bot:
                continue
            user_id = str(member.id)
            member_roles = set(r.name for r in member.roles)
            target_roles = auto_roles_set & member_roles
            if target_roles:
                current_holders[user_id] = list(target_roles)
        changes = {"removed": 0, "added": 0}
        users_to_remove = []
        for user_id, user_roles in list(bot.data.role_data[guild_id].items()):
            if user_id not in current_holders:
                users_to_remove.append(user_id)
                changes["removed"] += len(user_roles)
            else:
                for role_name in list(user_roles.keys()):
                    if role_name not in current_holders[user_id]:
                        del bot.data.role_data[guild_id][user_id][role_name]
                        changes["removed"] += 1
                if not bot.data.role_data[guild_id][user_id]:
                    users_to_remove.append(user_id)
        for user_id in users_to_remove:
            del bot.data.role_data[guild_id][user_id]
        for user_id, roles in current_holders.items():
            bot.data.role_data.setdefault(guild_id, {}).setdefault(user_id, {})
            for role_name in roles:
                if role_name not in bot.data.role_data[guild_id][user_id]:
                    bot.data.role_data[guild_id][user_id][role_name] = now
                    if role_name in ROLES_TO_AUTO_REMOVE:
                        bot.data.add_role_history(guild_id, user_id, role_name, now)
                    changes["added"] += 1
        if changes["removed"] or changes["added"]:
            await bot.data.save_all()
            sync_msg = f"{'定期' if is_periodic else '起動時'}同期: 削除{changes['removed']}件, 追加{changes['added']}件"
            await log_message(guild, sync_msg, "info")
        return changes
    except Exception as e:
        logger.error(f"Sync error for {guild.name}: {e}")
        return {"removed": 0, "added": 0}

async def process_role_removal(guild):
    guild_id = str(guild.id)
    if guild_id not in bot.data.role_data:
        return 0
    now = now_jst().timestamp()
    total_removed = 0
    changed = False
    async with bot.removal_lock:
        for user_id, user_roles in list(bot.data.role_data[guild_id].items()):
            member = guild.get_member(int(user_id))
            if not member:
                del bot.data.role_data[guild_id][user_id]
                changed = True
                continue
            roles_to_remove = []
            for role_name, timestamp in list(user_roles.items()):
                if role_name not in ROLES_TO_AUTO_REMOVE or not timestamp:
                    continue
                role = discord.utils.get(guild.roles, name=role_name)
                if not role or role not in member.roles:
                    bot.data.role_data[guild_id][user_id].pop(role_name, None)
                    changed = True
                    continue
                remove_seconds = bot.data.get_remove_seconds(guild_id, user_id, role_name)
                if now - timestamp >= remove_seconds:
                    roles_to_remove.append((role, role_name, remove_seconds, timestamp))
            for role, role_name, remove_seconds, timestamp in roles_to_remove:
                try:
                    if role not in member.roles:
                        continue
                    await member.remove_roles(role, reason=f"自動削除（{format_duration(remove_seconds)}経過）")
                    assigned_time = timestamp_to_jst(timestamp)
                    sec_passed = int(now - timestamp)
                    await log_message(
                        guild,
                        f"{member.display_name} から '{role_name}' を自動削除 "
                        f"(付与: {assigned_time.strftime('%Y/%m/%d %H:%M:%S')}, 経過: {format_duration(sec_passed)})",
                        "success"
                    )
                    bot.data.role_data[guild_id][user_id].pop(role_name, None)
                    total_removed += 1
                    changed = True
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Role removal error for {member}: {e}")
            if not bot.data.role_data[guild_id][user_id]:
                del bot.data.role_data[guild_id][user_id]
                changed = True
    if changed:
        await bot.data.save_all()
    return total_removed

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_roles():
    try:
        total_removed = 0
        for guild in bot.guilds:
            removed = await process_role_removal(guild)
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
        for guild in bot.guilds:
            await sync_data_with_reality(guild, True)
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
    for guild in bot.guilds:
        await log_message(guild, f"Bot起動完了 ({now_jst().strftime('%Y/%m/%d %H:%M:%S')} JST)", "success")
        await sync_data_with_reality(guild)
    await bot.data.save_all()
    if not check_roles.is_running():
        check_roles.start()
    if not sync_data_periodically.is_running():
        sync_data_periodically.start()
    await check_roles.coro()

def admin_required(func):
    @functools.wraps(func)
    async def wrapper(interaction, *args, **kwargs):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
            return
        return await func(interaction, *args, **kwargs)
    return wrapper

async def create_embed(title, color=0x0099ff, **fields):
    embed = discord.Embed(title=title, color=color)
    for name, value in fields.items():
        embed.add_field(name=name.replace('_', ' ').title(), value=value, inline=True)
    return embed

class ReasonModal(Modal, title="理由を編集"):
    def __init__(self, guild_id, user_id, role_name, index, old_reason, view_instance):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.role_name = role_name
        self.index = index
        self.view_instance = view_instance
        self.reason_input = TextInput(
            label="理由",
            style=discord.TextStyle.long,
            default=old_reason or "",
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value.strip()
        success = bot.data.edit_role_history_reason(self.guild_id, self.user_id, self.role_name, self.index, reason)
        if success:
            await bot.data.save_all()
            await interaction.response.send_message(
                f"✅ 理由を更新しました\n**{self.role_name} {self.index+1}回目:** {reason or '(理由なし)'}",
                ephemeral=True
            )
            await self.view_instance.update_view(interaction.message)
            # ログ追加
            guild = interaction.guild
            user = guild.get_member(int(self.user_id)) if guild else None
            user_name = user.display_name if user else self.user_id
            log_msg = f"{interaction.user.display_name} が {user_name} の '{self.role_name} {self.index+1}回目' 理由を編集: {reason or '(理由なし)'}"
            await log_message(guild, log_msg, "info")
        else:
            await interaction.response.send_message("❌ 理由の更新に失敗しました", ephemeral=True)

class EditReasonButton(Button):
    def __init__(self, guild_id, user_id, role_name, index, old_reason, view_instance, display_info):
        label = f"{'✏️' if old_reason else '➕'} {display_info}"
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.guild_id = guild_id
        self.user_id = user_id
        self.role_name = role_name
        self.index = index
        self.old_reason = old_reason
        self.view_instance = view_instance

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ReasonModal(self.guild_id, self.user_id, self.role_name, self.index, self.old_reason, self.view_instance)
        )

class NavigationButton(Button):
    def __init__(self, direction, disabled=False):
        super().__init__(
            emoji="◀️" if direction == "prev" else "▶️",
            label="前のページ" if direction == "prev" else "次のページ",
            style=discord.ButtonStyle.primary,
            disabled=disabled
        )
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if self.direction == "prev" and view.current_page > 0:
            view.current_page -= 1
        elif self.direction == "next" and view.current_page < view.total_pages - 1:
            view.current_page += 1
        await view.update_view(interaction.message, interaction)

class RoleHistoryView(View):
    def __init__(self, guild_id, user_id, user_name, history):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.user_id = user_id
        self.user_name = user_name
        self.history = history
        self.current_page = 0
        self.items_per_role_per_page = 5
        self._calc_pages()
        self.update_buttons()

    def _calc_pages(self):
        self.role_pages = {
            r: (len(items) + self.items_per_role_per_page - 1) // self.items_per_role_per_page
            for r, items in self.history.items()
        }
        self.total_pages = max(self.role_pages.values()) if self.role_pages else 1

    def get_current_page_data(self):
        page_data = {}
        for role_name, items in self.history.items():
            sorted_items = sorted(enumerate(items), key=lambda x: x[1]['timestamp'], reverse=True)
            start_idx = self.current_page * self.items_per_role_per_page
            end_idx = start_idx + self.items_per_role_per_page
            page_items = sorted_items[start_idx:end_idx] if start_idx < len(sorted_items) else []
            if page_items:
                page_items_with_index = [
                    {
                        'item': item,
                        'original_index': idx,
                        'display_number': idx + 1
                    }
                    for idx, item in page_items
                ]
                page_data[role_name] = {
                    'items': page_items_with_index,
                    'start_index': start_idx,
                    'total_count': len(items)
                }
        return page_data

    def update_buttons(self):
        self.clear_items()
        page_data = self.get_current_page_data()
        self.add_item(NavigationButton("prev", self.current_page == 0))
        self.add_item(NavigationButton("next", self.current_page >= self.total_pages - 1))
        role_rows = {"注意": 1, "警告": 2}
        for role_name, role_data in page_data.items():
            if role_name not in role_rows:
                continue
            for item_data in role_data['items'][:self.items_per_role_per_page]:
                entry = item_data['item']
                original_index = item_data['original_index']
                display_num = item_data['display_number']
                display_info = f"{role_name} {display_num}回目"
                b = EditReasonButton(self.guild_id, self.user_id, role_name, original_index, entry["reason"], self, display_info)
                b.row = role_rows[role_name]
                self.add_item(b)

    def create_embed(self):
        embed = discord.Embed(
            title=f"📝 {self.user_name} のロール付与履歴（注意・警告のみ）",
            color=0x0099ff
        )
        page_data = self.get_current_page_data()
        if not page_data:
            embed.description = "このページには表示する履歴がありません。"
            return embed
        for role_name, role_data in page_data.items():
            items_with_index = role_data['items']
            start_index = role_data['start_index']
            total_count = role_data['total_count']
            lines = []
            for item_data in items_with_index:
                entry = item_data['item']
                actual_count = item_data['display_number']
                dt = timestamp_to_jst(entry["timestamp"]).strftime('%Y/%m/%d %H:%M:%S')
                reason = entry["reason"] or "(理由未記入)"
                lines.append(f"**{actual_count}回目:** {dt}\n　理由：{reason}")
            display_start = start_index + 1
            display_end = start_index + len(items_with_index)
            page_info = f"（新しい順 {display_start}-{display_end}/{total_count}件）"
            field_name = f"{role_name} {page_info}"
            embed.add_field(name=field_name, value="\n".join(lines), inline=False)
        if self.total_pages > 1:
            embed.set_footer(text=f"ページ {self.current_page + 1}/{self.total_pages} （新しい順）")
        return embed

    async def update_view(self, message, interaction=None):
        self.history = bot.data.role_add_history.get(self.guild_id, {}).get(self.user_id, {})
        self._calc_pages()
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        self.update_buttons()
        embed = self.create_embed()
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await message.edit(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

@bot.tree.command(name="show_role_history", description="指定ユーザーのロール付与履歴表示（注意・警告のみ。理由編集機能付き）")
@app_commands.describe(user="履歴を表示したいユーザー（省略時は自分）")
async def show_role_history(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    guild_id, user_id = str(interaction.guild.id), str(user.id)
    history = bot.data.role_add_history.get(guild_id, {}).get(user_id, {})
    if not history:
        embed = discord.Embed(
            title=f"📝 {user.display_name} のロール付与履歴（注意・警告のみ）",
            description="履歴がありません。",
            color=0x0099ff
        )
        await interaction.response.send_message(embed=embed)
        return
    view = RoleHistoryView(guild_id, user_id, user.display_name, history)
    embed = view.create_embed()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="giveall", description="全員に指定ロールを付与（管理者限定）")
@app_commands.describe(role="付与するロール")
async def giveall(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message("❌ そのロールは付与できません", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    members = [m for m in interaction.guild.members if not m.bot and role not in m.roles]
    if not members:
        await interaction.followup.send("✅ 全員が既にロールを持っています。")
        return
    progress_msg = await interaction.followup.send(f"🔄 {role.name} を {len(members)} 人に付与中...")
    success = 0                                                 
    for i, member in enumerate(members):
        if await add_role_with_timestamp(member, role, f"一括付与 by {interaction.user.display_name}"):
            success += 1
        if (i + 1) % BATCH_SIZE == 0:
            await progress_msg.edit(content=f"🔄 進行状況: {i + 1}/{len(members)}")
            await asyncio.sleep(API_DELAY)
        else:
            await asyncio.sleep(0.1)
    result = f"✅ {role.name} 付与完了！成功: {success}人"
    if role.name in ROLES_TO_AUTO_REMOVE:
        seconds = bot.data.settings["remove_seconds"].get(role.name, DEFAULT_REMOVE_SECONDS[role.name])
        result += f"\n⏰ {format_duration(seconds)}後に自動削除"
    await progress_msg.edit(content=result)
    await log_message(interaction.guild, f"{interaction.user.display_name} が {role.name} を一括付与: {success}人", "success")

@bot.tree.command(name="test_add", description="自分にロール付与（テスト用）")
@app_commands.describe(role="付与するロール")
async def test_add(interaction: discord.Interaction, role: discord.Role):
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message("❌ そのロールは付与できません", ephemeral=True)
        return
    if role in interaction.user.roles:
        await interaction.response.send_message(f"ℹ️ 既に {role.name} を持っています", ephemeral=True)
        return
    result = await add_role_with_timestamp(interaction.user, role, "テストコマンド")
    if result:
        msg = f"✅ {role.name} を付与しました"
        if role.name in ROLES_TO_AUTO_REMOVE:
            seconds = bot.data.get_remove_seconds(str(interaction.guild.id), str(interaction.user.id), role.name)
            msg += f"\n⏰ {format_duration(seconds)}後に自動削除"
        await interaction.response.send_message(msg)
    else:
        await interaction.response.send_message("❌ 付与に失敗しました", ephemeral=True)

@bot.tree.command(name="status", description="Bot状態表示")
async def status(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    tracked = len(bot.data.role_data.get(guild_id, {}))
    log_channel_id = bot.data.guild_log_channels.get(guild_id)
    log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None
    log_channel_disp = log_channel.mention if log_channel else "未設定"
    debug_mode = "ON" if DEBUG else "OFF"
    embed = await create_embed(
        "Bot ステータス", 0x00ff00,
        追跡中ユーザー=f"{tracked}人",
        チェック間隔=f"{CHECK_INTERVAL//60}分",
        同期間隔=f"{SYNC_INTERVAL//60}分",
        タイムゾーン="日本時間 (JST)",
        ログ送信先=log_channel_disp,
        デバッグモード=debug_mode
    )
    remove_info = [f"{role}: {format_duration(bot.data.settings['remove_seconds'].get(role, DEFAULT_REMOVE_SECONDS[role]))}" for role in ROLES_TO_AUTO_REMOVE]
    embed.add_field(name="自動削除期間", value="\n".join(remove_info), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="set_remove_period", description="デフォルト削除期間設定（管理者限定）")
@app_commands.describe(role="ロール名", days="日", hours="時間", minutes="分", seconds="秒")
@app_commands.choices(role=[app_commands.Choice(name=r, value=r) for r in ROLES_TO_AUTO_REMOVE])
@admin_required
async def set_remove_period(
    interaction: discord.Interaction,
    role: str,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0
):
    total_seconds = parse_duration(days, hours, minutes, seconds)
    if total_seconds < 0:
        await interaction.response.send_message("❌ 期間は0以上で指定してください", ephemeral=True)
        return
    old_seconds = bot.data.settings["remove_seconds"].get(role, DEFAULT_REMOVE_SECONDS[role])
    bot.data.settings["remove_seconds"][role] = total_seconds
    await bot.data.save_all()
    embed = await create_embed(
        "✅ デフォルト削除期間設定完了", 0x00ff00,
        ロール=role,
        変更前=format_duration(old_seconds),
        変更後=format_duration(total_seconds)
    )
    await interaction.response.send_message(embed=embed)
    await log_message(interaction.guild, f"{interaction.user.display_name} が '{role}' 期間を {format_duration(old_seconds)}→{format_duration(total_seconds)}に変更", "info")

@bot.tree.command(name="adjust_remove_time", description="個人のロール削除までの残り時間を増加・減少・セット（管理者限定）")
@app_commands.describe(
    user="対象ユーザー",
    role="ロール名",
    action="操作（増加/減少/セット）",
    days="日",
    hours="時間",
    minutes="分",
    seconds="秒"
)
@app_commands.choices(role=[app_commands.Choice(name=r, value=r) for r in ROLES_TO_AUTO_REMOVE])
@app_commands.choices(action=[
    app_commands.Choice(name="増加", value="add"),
    app_commands.Choice(name="減少", value="sub"),
    app_commands.Choice(name="セット", value="set")
])
@admin_required
async def adjust_remove_time(
    interaction: discord.Interaction,
    user: discord.Member,
    role: str,
    action: str,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0
):
    guild_id, user_id = str(interaction.guild.id), str(user.id)
    role_data = bot.data.role_data.get(guild_id, {}).get(user_id, {})
    if role not in role_data:
        await interaction.response.send_message(f"❌ {user.display_name} は現在 {role} を持っていません。", ephemeral=True)
        return
    now = now_jst().timestamp()
    assigned_ts = role_data[role]
    remove_seconds = bot.data.get_remove_seconds(guild_id, user_id, role)
    remain = assigned_ts + remove_seconds - now
    if remain <= 0:
        await interaction.response.send_message(f"❌ 既に削除対象です。", ephemeral=True)
        return
    delta = parse_duration(days, hours, minutes, seconds)
    if action == "add":
        new_remain = remain + delta
    elif action == "sub":
        new_remain = max(0, remain - delta)
    elif action == "set":
        new_remain = max(0, delta)
    else:
        await interaction.response.send_message("❌ 不正な操作です。", ephemeral=True)
        return
    if new_remain <= 0:
        # 0秒セット時は個人設定削除（デフォルトに戻す）
        removed = bot.data.remove_user_setting(guild_id, user_id, role)
        await bot.data.save_all()
        msg = f"✅ {user.display_name} の {role} の個人削除期間設定を削除しデフォルトに戻しました。"
        await interaction.response.send_message(msg)
        await log_message(interaction.guild, f"{interaction.user.display_name} が {user.display_name} の {role} の個人削除期間設定を削除", "info")
        return
    # 付与時刻はそのまま、個人削除期間を今からnew_remain秒に設定
    bot.data.set_user_remove_seconds(guild_id, user_id, role, int(now - assigned_ts + new_remain))
    await bot.data.save_all()
    msg = f"✅ {user.display_name} の {role} の残り時間を {format_duration(remain)} → {format_duration(new_remain)} に{('増加' if action=='add' else '減少' if action=='sub' else 'セット')}しました。"
    await interaction.response.send_message(msg)
    await log_message(interaction.guild, f"{interaction.user.display_name} が {user.display_name} の {role} の残り時間を {format_duration(remain)} → {format_duration(new_remain)} に{('増加' if action=='add' else '減少' if action=='sub' else 'セット')}", "info")

@bot.tree.command(name="sync_check", description="手動同期・チェック実行（管理者限定）")
@admin_required
async def sync_check(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await sync_data_with_reality(interaction.guild)
    removed = await process_role_removal(interaction.guild)
    await bot.data.save_all()
    await interaction.followup.send(f"✅ 手動同期完了\n削除されたロール: {removed}個")
    await log_message(interaction.guild, f"{interaction.user.display_name} が手動同期実行: {removed}個削除", "info")

@bot.tree.command(name="set_log_channel", description="このチャンネルをログ送信先に設定（管理者限定）")
@admin_required
async def set_log_channel(interaction: discord.Interaction):
    bot.data.guild_log_channels[str(interaction.guild.id)] = interaction.channel.id
    await bot.data.save_all()
    await interaction.response.send_message(f"✅ ログ送信先を {interaction.channel.mention} に設定しました", ephemeral=True)
    # ログ追加
    await log_message(interaction.guild, f"{interaction.user.display_name} がログ送信先を {interaction.channel.mention} に設定", "info")

@bot.tree.command(name="message", description="指定したチャンネルにメッセージを送信")
@app_commands.describe(
    content="送信するメッセージ内容",
    channel="送信先チャンネル（名前またはID、省略時は実行したチャンネル）"
)
async def message_command(
    interaction: discord.Interaction,
    content: str,
    channel: str = None
):
    target_channel = None
    if channel:
        ch = discord.utils.get(interaction.guild.text_channels, name=channel)
        if ch:
            target_channel = ch
        else:
            try:
                channel_id = int(channel)
                ch = interaction.guild.get_channel(channel_id)
                if ch and ch.type == discord.ChannelType.text:
                    target_channel = ch
            except ValueError:
                pass
    if not target_channel:
        target_channel = interaction.channel
    if not target_channel.permissions_for(interaction.guild.me).send_messages:
        await interaction.response.send_message(
            f"❌ {target_channel.mention} にメッセージを送信できません（権限不足）",
            ephemeral=True
        )
        return
    try:
        await target_channel.send(content)
        await interaction.response.send_message(
            f"✅ メッセージを {target_channel.mention} に送信しました", ephemeral=True
        )
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        await interaction.response.send_message(
            f"❌ メッセージ送信に失敗しました: {e}", ephemeral=True
        )

@bot.tree.command(name="help", description="コマンド一覧表示")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 コマンド一覧", color=0x0099ff)
    commands_info = {
        "/giveall": "全員にロール付与（管理者限定）",
        "/test_add": "自分にロール付与（テスト用）",
        "/status": "Bot状態表示",
        "/set_remove_period": "デフォルト削除期間設定（管理者限定）",
        "/adjust_remove_time": "個人のロール削除までの残り時間を増加・減少・セット（管理者限定）",
        "/show_remove_time": "自動削除ロールの残り時間を表示",
        "/show_role_history": "ロール付与履歴表示（注意・警告のみ。理由編集機能付き）",
        "/sync_check": "手動同期・チェック（管理者限定）",
        "/set_log_channel": "このチャンネルをログ送信先に設定（管理者限定）",
        "/message": "指定したチャンネルにメッセージ送信"
    }
    for cmd, desc in commands_info.items():
        embed.add_field(name=cmd, value=desc, inline=False)
    embed.add_field(
        name="⚠️ 重要事項",
        value=(
            "• 自動削除対象: " + ", ".join(ROLES_TO_AUTO_REMOVE) +
            "\n• 不定期起動対応"
            "\n• ロール付与履歴確認・理由編集可能（注意・警告のみ）"
            "\n• ページネーション対応（各ロール5件ずつ表示）"
            "\n• 理由編集後の自動更新機能"
            "\n• ログ送信先チャンネルをサーバーごとに設定可能"
            "\n• `/message` で好きなメッセージを任意のチャンネルに投稿可能"
            "\n   例: `/message Hello world! general`"
            "\n• `/adjust_remove_time` で個人のロール削除までの残り時間を柔軟に調整可能"
            "\n• `/show_remove_time` で自動削除ロールの残り時間を確認可能"
        ),
        inline=False
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="show_remove_time", description="指定ユーザーの自動削除ロールの残り時間を表示")
@app_commands.describe(user="対象ユーザー（省略時は自分）")
async def show_remove_time(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    guild_id, user_id = str(interaction.guild.id), str(user.id)
    role_data = bot.data.role_data.get(guild_id, {}).get(user_id, {})
    now = now_jst().timestamp()
    embed = discord.Embed(title=f"⏰ {user.display_name} のロール削除までの残り時間", color=0x0099ff)
    found = False
    for role_name in ROLES_TO_AUTO_REMOVE:
        if role_name in role_data:
            assigned_ts = role_data[role_name]
            remove_seconds = bot.data.get_remove_seconds(guild_id, user_id, role_name)
            remain = int(assigned_ts + remove_seconds - now)
            if remain > 0:
                embed.add_field(name=role_name, value=f"残り: {format_duration(remain)}", inline=True)
            else:
                embed.add_field(name=role_name, value="削除対象（まもなく削除）", inline=True)
            found = True
        else:
            embed.add_field(name=role_name, value="未付与", inline=True)
    if not found:
        embed.description = "自動削除対象ロールは付与されていません。"
    await interaction.response.send_message(embed=embed)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f"Application command error: {error}", exc_info=True)
    if not interaction.response.is_done():
        await interaction.response.send_message("❌ 予期しないエラーが発生しました。", ephemeral=True)

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