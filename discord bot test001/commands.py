# -*- coding: utf-8 -*-
import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
import logging
import functools
import asyncio
import os

from config import ROLES_TO_AUTO_REMOVE, DEFAULT_REMOVE_SECONDS, BATCH_SIZE, API_DELAY, DATA_FILE, BACKUP_DIR
from helpers import now_jst, format_duration, parse_duration, timestamp_to_jst, validate_role_data
import datetime as _dt

logger = logging.getLogger(__name__)

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
    def __init__(self, guild_id, user_id, role_name, index, old_reason, view_instance, bot):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.role_name = role_name
        self.index = index
        self.view_instance = view_instance
        self.bot = bot
        self.reason_input = TextInput(
            label="理由",
            style=discord.TextStyle.long,
            default=old_reason or "",
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        from core import log_message
        reason = self.reason_input.value.strip()
        success = self.bot.data.edit_role_history_reason(self.guild_id, self.user_id, self.role_name, self.index, reason)
        if success:
            await self.bot.data.save_all()
            await interaction.response.send_message(
                f"✅ 理由を更新しました\n**{self.role_name} {self.index+1}回目:** {reason or '(理由なし)'}",
                ephemeral=True
            )
            await self.view_instance.update_view(interaction.message)
            guild = interaction.guild
            user = guild.get_member(int(self.user_id)) if guild else None
            user_name = user.display_name if user else self.user_id
            log_msg = f"{interaction.user.display_name} が {user_name} の '{self.role_name} {self.index+1}回目' 理由を編集: {reason or '(理由なし)'}"
            await log_message(self.bot, guild, log_msg, "info")
        else:
            await interaction.response.send_message("❌ 理由の更新に失敗しました", ephemeral=True)

class EditReasonButton(Button):
    def __init__(self, guild_id, user_id, role_name, index, old_reason, view_instance, display_info, bot):
        label = f"{'✏️' if old_reason else '➕'} {display_info}"
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.guild_id = guild_id
        self.user_id = user_id
        self.role_name = role_name
        self.index = index
        self.old_reason = old_reason
        self.view_instance = view_instance
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ReasonModal(self.guild_id, self.user_id, self.role_name, self.index, self.old_reason, self.view_instance, self.bot)
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
    def __init__(self, guild_id, user_id, user_name, history, bot):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.user_id = user_id
        self.user_name = user_name
        self.history = history
        self.bot = bot
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
                b = EditReasonButton(self.guild_id, self.user_id, role_name, original_index, entry["reason"], self, display_info, self.bot)
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
        self.history = self.bot.data.role_add_history.get(self.guild_id, {}).get(self.user_id, {})
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

def _backup_current_file_to_dir(src_path: str, prefix: str):
    """現在のファイル(src_path)をBACKUP_DIRへタイムスタンプ付きでコピー（存在する場合のみ）"""
    import shutil
    try:
        if not os.path.exists(src_path):
            return None
        ts = now_jst().strftime("%Y%m%d_%H%M%S")
        dst_name = f"{prefix}{ts}.json"
        dst_path = os.path.join(BACKUP_DIR, dst_name)
        shutil.copy2(src_path, dst_path)
        return dst_path
    except Exception as e:
        logger.error(f"Backup current file failed: {e}")
        return None

def _compose_backup_filename(data_type: str, timestamp: str) -> str:
    """data_type + timestamp -> backup filename"""
    prefix_map = {
        "roles_data": "roles_data_",
        "settings": "settings_",
        "role_history": "role_history_",
        "log_channel": "log_channel_",
        "tenure_rules": "tenure_rules_",
    }
    prefix = prefix_map.get(data_type)
    if not prefix:
        return ""
    return f"{prefix}{timestamp}.json"

def _data_type_to_file(data_type: str) -> str:
    from config import DATA_FILE, SETTINGS_FILE, ROLE_HISTORY_FILE, LOG_CHANNEL_FILE, TENURE_RULES_FILE
    mapping = {
        "roles_data": DATA_FILE,
        "settings": SETTINGS_FILE,
        "role_history": ROLE_HISTORY_FILE,
        "log_channel": LOG_CHANNEL_FILE,
        "tenure_rules": TENURE_RULES_FILE,
    }
    return mapping.get(data_type, "")

def _validate_timestamp_format(ts: str) -> bool:
    try:
        _dt.datetime.strptime(ts, "%Y%m%d_%H%M%S")
        return True
    except Exception:
        return False

def setup_commands(bot):
    """すべてのスラッシュコマンドを登録"""
    
    @bot.tree.command(name="giveall", description="全員に指定ロールを付与（管理者限定）")
    @app_commands.describe(role="付与するロール")
    async def giveall(interaction: discord.Interaction, role: discord.Role):
        from core import add_role_with_timestamp, log_message
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
            if await add_role_with_timestamp(bot, member, role, f"一括付与 by {interaction.user.display_name}"):
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
        await log_message(bot, interaction.guild, f"{interaction.user.display_name} が {role.name} を一括付与: {success}人", "success")

    @bot.tree.command(name="test_add", description="自分にロール付与（テスト用）")
    @app_commands.describe(role="付与するロール")
    async def test_add(interaction: discord.Interaction, role: discord.Role):
        from core import add_role_with_timestamp
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ そのロールは付与できません", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(f"ℹ️ 既に {role.name} を持っています", ephemeral=True)
            return
        result = await add_role_with_timestamp(bot, interaction.user, role, "テストコマンド")
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
        from config import CHECK_INTERVAL, SYNC_INTERVAL, DEBUG
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
        from core import log_message
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
        await log_message(bot, interaction.guild, f"{interaction.user.display_name} が '{role}' 期間を {format_duration(old_seconds)}→{format_duration(total_seconds)}に変更", "info")

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
        from core import log_message
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
            removed = bot.data.remove_user_setting(guild_id, user_id, role)
            await bot.data.save_all()
            msg = f"✅ {user.display_name} の {role} の個人削除期間設定を削除しデフォルトに戻しました。"
            await interaction.response.send_message(msg)
            await log_message(bot, interaction.guild, f"{interaction.user.display_name} が {user.display_name} の {role} の個人削除期間設定を削除", "info")
            return
        bot.data.set_user_remove_seconds(guild_id, user_id, role, int(now - assigned_ts + new_remain))
        await bot.data.save_all()
        msg = f"✅ {user.display_name} の {role} の残り時間を {format_duration(remain)} → {format_duration(new_remain)} に{('増加' if action=='add' else '減少' if action=='sub' else 'セット')}しました。"
        await interaction.response.send_message(msg)
        await log_message(bot, interaction.guild, f"{interaction.user.display_name} が {user.display_name} の {role} の残り時間を {format_duration(remain)} → {format_duration(new_remain)} に{('増加' if action=='add' else '減少' if action=='sub' else 'セット')}", "info")

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

    @bot.tree.command(name="show_role_history", description="指定ユーザーのロール付与履歴表示（注意・警告のみ。理由編集機能付き）")
    @app_commands.describe(user="履歴を表示したいユーザー（省略時は自分）")
    async def show_role_history(interaction: discord.Interaction, user: discord.Member = None):
        user = user or interaction.user
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)
        history = bot.data.role_add_history.get(guild_id, {}).get(user_id, {})
        if not history:
            embed = discord.Embed(
                title=f"📝 {user.display_name} のロール付与履歴（注意・警告のみ）",
                description="履歴がありません。",
                color=0x0099ff
            )
            await interaction.response.send_message(embed=embed)
            return
        view = RoleHistoryView(guild_id, user_id, user.display_name, history, bot)
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view)

    @bot.tree.command(name="sync_check", description="手動同期・チェック実行（管理者限定）")
    @admin_required
    async def sync_check(interaction: discord.Interaction):
        from core import sync_data_with_reality, process_role_removal, log_message
        await interaction.response.defer(thinking=True)
        await sync_data_with_reality(bot, interaction.guild)
        removed = await process_role_removal(bot, interaction.guild)
        await bot.data.save_all()
        await interaction.followup.send(f"✅ 手動同期完了\n削除されたロール: {removed}個")
        await log_message(bot, interaction.guild, f"{interaction.user.display_name} が手動同期実行: {removed}個削除", "info")

    @bot.tree.command(name="set_log_channel", description="このチャンネルをログ送信先に設定（管理者限定）")
    @admin_required
    async def set_log_channel(interaction: discord.Interaction):
        from core import log_message
        bot.data.guild_log_channels[str(interaction.guild.id)] = interaction.channel.id
        await bot.data.save_all()
        await interaction.response.send_message(f"✅ ログ送信先を {interaction.channel.mention} に設定しました", ephemeral=True)
        await log_message(bot, interaction.guild, f"{interaction.user.display_name} がログ送信先を {interaction.channel.mention} に設定", "info")

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

    @bot.tree.command(name="set_mention_role", description="メンションコマンドの設定（管理者限定）")
    @app_commands.describe(
        mention_role="メンション対象のロール",
        required_role="実行に必要なロール（省略時は誰でも実行可能）"
    )
    @admin_required
    async def set_mention_role(
        interaction: discord.Interaction,
        mention_role: discord.Role,
        required_role: discord.Role = None
    ):
        from core import log_message
        guild_id = str(interaction.guild.id)
        bot.data.mention_config.setdefault(guild_id, {})
        
        old_config = bot.data.mention_config[guild_id].copy() if guild_id in bot.data.mention_config else {}
        
        bot.data.mention_config[guild_id] = {
            "mention_role_id": mention_role.id,
            "mention_role_name": mention_role.name,
            "required_role_id": required_role.id if required_role else None,
            "required_role_name": required_role.name if required_role else "（誰でも実行可能）"
        }
        
        await bot.data.save_all()
        
        old_info = f"メンション: {old_config.get('mention_role_name', 'なし')}, 権限: {old_config.get('required_role_name', 'なし')}" if old_config else "ルールなし"
        
        embed = await create_embed(
            "✅ メンション設定完了", 0x00ff00,
            メンション対象ロール=mention_role.name,
            実行権限ロール=required_role.name if required_role else "誰でも実行可能",
            変更前=old_info
        )
        
        await interaction.response.send_message(embed=embed)
        await log_message(
            bot, interaction.guild,
            f"{interaction.user.display_name} がメンション設定を変更: {mention_role.name} / 権限: {required_role.name if required_role else '誰でも'}",
            "info"
        )

    @bot.tree.command(name="mention", description="「勧誘歓迎」ロールをメンション")
    async def mention(interaction: discord.Interaction):
        from core import log_message
        guild_id = str(interaction.guild.id)
        
        if guild_id not in bot.data.mention_config:
            await interaction.response.send_message("❌ メンション設定がまだされていません。管理者が `/set_mention_role` で設定してください。", ephemeral=True)
            return
        
        config = bot.data.mention_config[guild_id]
        required_role_id = config.get("required_role_id")
        mention_role_id = config.get("mention_role_id")
        
        # 権限チェック
        if required_role_id:
            required_role = interaction.guild.get_role(required_role_id)
            if not required_role or required_role not in interaction.user.roles:
                await interaction.response.send_message(
                    f"❌ このコマンドを実行するには {config.get('required_role_name', 'unknown')} ロールが必要です。",
                    ephemeral=True
                )
                return
        
        # メンション対象ロール取得
        mention_role = interaction.guild.get_role(mention_role_id)
        if not mention_role:
            await interaction.response.send_message("❌ メンション対象ロールが見つかりません。管理者に報告してください。", ephemeral=True)
            return
        
        # メンション送信
        try:
            await interaction.response.send_message(f"{mention_role.mention}")
            await log_message(
                bot, interaction.guild,
                f"{interaction.user.display_name} がメンションコマンドを実行: {mention_role.name}",
                "info"
            )
        except Exception as e:
            logger.error(f"Mention command error: {e}")
            await interaction.response.send_message("❌ メンション送信に失敗しました。", ephemeral=True)

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
            "/set_tenure_rule": "テニュアルール設定（管理者限定）",
            "/show_tenure_rules": "テニュアルール一覧表示",
            "/delete_tenure_rule": "テニュアルール削除（管理者限定）",
            "/restore_backup": "バックアップから復元（管理者限定）",
            "/set_mention_role": "メンション設定（管理者限定）",
            "/mention": "設定ロールをメンション",
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
                "\n• **テニュアルール機能: 特定ロール付与時に参加期間をチェック**"
                "\n• `/set_tenure_rule` でトリガーロール→対象ロール マッピング設定可能"
                "\n• 例: 'チェック' ロール付与時、参加90日以上なら 'メンバー' ロール自動付与"
                "\n• ログ送信先チャンネルをサーバーごとに設定可能"
                "\n• **メンション機能: `/set_mention_role` でメンション対象ロール設定後、`/mention` で実行**"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="set_tenure_rule", description="トリガーロール付与時のテニュアベース自動付与ルール設定（管理者限定）")
    @app_commands.describe(
        trigger_role="この役割が付与されたときにチェック",
        target_role="付与対象の役割",
        tenure_days="サーバー参加からの経過日数"
    )
    @admin_required
    async def set_tenure_rule(
        interaction: discord.Interaction,
        trigger_role: discord.Role,
        target_role: discord.Role,
        tenure_days: int = 90
    ):
        from core import log_message
        if tenure_days < 1:
            await interaction.response.send_message("❌ 参加日数は1日以上で指定してください", ephemeral=True)
            return
        
        guild_id = str(interaction.guild.id)
        bot.data.tenure_rules.setdefault(guild_id, {})
        
        old_rule = bot.data.tenure_rules[guild_id].get(trigger_role.name)
        
        bot.data.tenure_rules[guild_id][trigger_role.name] = {
            "target_role": target_role.name,
            "tenure_days": tenure_days
        }
        
        await bot.data.save_all()
        
        old_info = f"対象役割: {old_rule['target_role']}, 期間: {old_rule['tenure_days']}日" if old_rule else "ルールなし"
        
        embed = await create_embed(
            "✅ テニュアルール設定完了", 0x00ff00,
            トリガー役割=trigger_role.name,
            対象役割=target_role.name,
            参加経過日数=f"{tenure_days}日以上",
            変更前=old_info
        )
        
        await interaction.response.send_message(embed=embed)
        await log_message(
            bot, interaction.guild,
            f"{interaction.user.display_name} が テニュアルールを設定: {trigger_role.name} → {target_role.name} ({tenure_days}日以上)",
            "info"
        )

    @bot.tree.command(name="show_tenure_rules", description="設定されているテニュアルール一覧表示")
    async def show_tenure_rules(interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        rules = bot.data.tenure_rules.get(guild_id, {})
        
        embed = discord.Embed(
            title="📋 テニュアルール一覧",
            color=0x0099ff,
            description="トリガーロール付与時に参加期間をチェックして追加ロールを付与"
        )
        
        if not rules:
            embed.description += "\n\n⚠️ ルール設定がありません"
            await interaction.response.send_message(embed=embed)
            return
        
        for trigger_role, rule in rules.items():
            target_role = rule.get("target_role", "不明")
            tenure_days = rule.get("tenure_days", 90)
            embed.add_field(
                name=f"🔔 {trigger_role}",
                value=f"→ **{target_role}** (参加{tenure_days}日以上で自動付与)",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="delete_tenure_rule", description="テニュアルールを削除（管理者限定）")
    @app_commands.describe(trigger_role="削除するトリガー役割")
    @admin_required
    async def delete_tenure_rule(interaction: discord.Interaction, trigger_role: discord.Role):
        from core import log_message
        guild_id = str(interaction.guild.id)
        rules = bot.data.tenure_rules.get(guild_id, {})
        
        if trigger_role.name not in rules:
            await interaction.response.send_message(
                f"❌ '{trigger_role.name}' のテニュアルール設定が見つかりません",
                ephemeral=True
            )
            return
        
        old_rule = rules[trigger_role.name]
        del bot.data.tenure_rules[guild_id][trigger_role.name]
        
        if not bot.data.tenure_rules[guild_id]:
            del bot.data.tenure_rules[guild_id]
        
        await bot.data.save_all()
        
        embed = await create_embed(
            "✅ テニュアルール削除完了", 0x00ff00,
            トリガー役割=trigger_role.name,
            対象役割=old_rule.get("target_role"),
            参加経過日数=f"{old_rule.get('tenure_days', 90)}日"
        )
        
        await interaction.response.send_message(embed=embed)
        await log_message(
            bot, interaction.guild,
            f"{interaction.user.display_name} が テニュアルール削除: {trigger_role.name}",
            "info"
        )

    @bot.tree.command(name="restore_backup", description="バックアップからデータ復元（管理者限定）")
    @app_commands.describe(data_type="復元するデータ種別", timestamp="バックアップのタイムスタンプ (YYYYMMDD_HHMMSS)")
    @app_commands.choices(data_type=[
        app_commands.Choice(name="roles_data", value="roles_data"),
        app_commands.Choice(name="settings", value="settings"),
        app_commands.Choice(name="role_history", value="role_history"),
        app_commands.Choice(name="log_channel", value="log_channel"),
        app_commands.Choice(name="tenure_rules", value="tenure_rules"),
    ])
    @admin_required
    async def restore_backup(interaction: discord.Interaction, data_type: str, timestamp: str):
        from core import log_message
        import shutil
        await interaction.response.defer(thinking=True)
        if not _validate_timestamp_format(timestamp):
            await interaction.followup.send("❌ タイムスタンプ形式が不正です。YYYYMMDD_HHMMSS の形式で指定してください。", ephemeral=True)
            return

        backup_filename = _compose_backup_filename(data_type, timestamp)
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        if not os.path.exists(backup_path):
            await interaction.followup.send(f"❌ 指定されたバックアップが見つかりません: {backup_filename}", ephemeral=True)
            return

        target_file = _data_type_to_file(data_type)
        if not target_file:
            await interaction.followup.send("❌ 不正なデータ種別です。", ephemeral=True)
            return

        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            pre_backup = _backup_current_file_to_dir(target_file, backup_filename.split('_')[0] + "_pre_")
            shutil.copy2(backup_path, target_file)
            post_backup = _backup_current_file_to_dir(target_file, backup_filename.split('_')[0] + "_restored_")
            bot.data.load_all()
            await interaction.followup.send(
                f"✅ 復元完了: {data_type}\n"
                f"指定バックアップ: {backup_filename}\n"
                f"復元前バックアップ: {os.path.basename(pre_backup) if pre_backup else 'なし'}\n"
                f"復元後バックアップ: {os.path.basename(post_backup) if post_backup else 'なし'}"
            )
            await log_message(bot, interaction.guild, f"{interaction.user.display_name} がバックアップから復元: {data_type} ← {backup_filename}", "info")
        except Exception as e:
            logger.error(f"Restore backup failed: {e}")
            await interaction.followup.send(f"❌ 復元に失敗しました: {e}", ephemeral=True)

def setup_command_error_handler(bot):
    """コマンドエラーハンドラーを登録"""
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Application command error: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ 予期しないエラーが発生しました。", ephemeral=True)
