# -*- coding: utf-8 -*-
import discord
import asyncio
import logging
from config import DATA_FILE, ROLES_TO_AUTO_REMOVE
from helpers import now_jst, timestamp_to_jst, format_duration, is_valid_guild_data, validate_role_data

logger = logging.getLogger(__name__)

async def log_message(bot, guild, message, level="info"):
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

async def add_role_with_timestamp(bot, member, role, reason=None):
    try:
        guild_id, user_id = str(member.guild.id), str(member.id)
        bot.data.role_data.setdefault(guild_id, {}).setdefault(user_id, {})
        if role in member.roles:
            return True
        now_ts = now_jst().timestamp()
        if role.name in ROLES_TO_AUTO_REMOVE:
            bot.data.remove_user_setting(guild_id, user_id, role.name)
        if role.name in ROLES_TO_AUTO_REMOVE and role.name not in bot.data.role_data[guild_id][user_id]:
            bot.data.role_data[guild_id][user_id][role.name] = now_ts
            bot.data.add_role_history(guild_id, user_id, role.name, now_ts)
        elif role.name not in bot.data.role_data[guild_id][user_id]:
            bot.data.role_data[guild_id][user_id][role.name] = now_ts
        await member.add_roles(role, reason=reason or "自動ロール付与")
        await bot.data.save_all()
        
        await check_and_apply_tenure_role(bot, member, role)
        
        return True
    except Exception as e:
        logger.error(f"Role add error for {member}: {e}")
        return False

async def check_and_apply_tenure_role(bot, member, trigger_role):
    """トリガーロール付与時に、メンバーの参加期間をチェックして対象ロールを付与し、トリガーロールを削除"""
    guild_id = str(member.guild.id)
    if guild_id not in bot.data.tenure_rules:
        return

    rules = bot.data.tenure_rules[guild_id]
    trigger_role_name = trigger_role.name

    if trigger_role_name not in rules:
        return

    rule = rules[trigger_role_name]
    target_role_name = rule.get("target_role")
    tenure_days = rule.get("tenure_days", 90)

    if not target_role_name:
        return

    member_tenure_days = (now_jst() - member.joined_at).days if member.joined_at else 0

    if member_tenure_days >= tenure_days:
        target_role = discord.utils.get(member.guild.roles, name=target_role_name)
        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(
                    target_role,
                    reason=f"テニュアルール: {trigger_role_name} 付与時、参加期間{tenure_days}日以上で自動付与"
                )
                await log_message(
                    bot, member.guild,
                    f"{member.display_name} は参加から{member_tenure_days}日経過しており、{trigger_role_name} 付与時に {target_role_name} を自動付与",
                    "success"
                )
            except Exception as e:
                logger.error(f"Tenure role assignment error for {member}: {e}")

    try:
        if trigger_role in member.roles:
            await member.remove_roles(trigger_role, reason="テニュアルール処理後に自動削除")
            await log_message(
                bot, member.guild,
                f"{member.display_name} からトリガーロール '{trigger_role_name}' を自動削除",
                "info"
            )
    except Exception as e:
        logger.error(f"Trigger role removal error for {member}: {e}")

async def sync_data_with_reality(bot, guild, is_periodic=False):
    try:
        if not guild.chunked or len(guild.members) == 0:
            logger.warning(f"[{guild.name}] メンバー情報が不完全のため同期をスキップしました。")
            return {"removed": 0, "added": 0}
        
        guild_id = str(guild.id)
        if not is_valid_guild_data(guild_id):
            logger.error(f"Invalid guild_id: {guild_id}")
            return {"removed": 0, "added": 0}
        
        try:
            current_role_data = bot.data._load_json(DATA_FILE, {})
            if not validate_role_data(current_role_data):
                logger.error(f"[{guild.name}] ロールデータの検証に失敗しました。同期をスキップします。")
                return {"removed": 0, "added": 0}
            bot.data.role_data = current_role_data
        except Exception as e:
            logger.error(f"[{guild.name}] ファイル再読み込み失敗: {e}。同期をスキップします。")
            return {"removed": 0, "added": 0}
        
        now = now_jst().timestamp()
        bot.data.role_data.setdefault(guild_id, {})
        current_holders = {}
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

        # 変更があれば保存とログ
        if changes["removed"] or changes["added"]:
            await bot.data.save_all()
            sync_msg = f"{'定期' if is_periodic else '起動時'}同期: 削除{changes['removed']}件, 追加{changes['added']}件"
            await log_message(guild, sync_msg, "info")

        # --- 追加: テニュアルールのトリガーロールを持つメンバーを検知して処理 ---
        # これで削除予定だったトリガーロールも正常に処理される
        tenure_rules = bot.data.tenure_rules.get(guild_id, {})
        if tenure_rules:
            trigger_role_names = set(tenure_rules.keys())
            for member in guild.members:
                if member.bot:
                    continue
                member_role_names = set(r.name for r in member.roles)
                for trigger_role_name in trigger_role_names & member_role_names:
                    trigger_role_obj = discord.utils.get(guild.roles, name=trigger_role_name)
                    if trigger_role_obj:
                        try:
                            await check_and_apply_tenure_role(bot, member, trigger_role_obj)
                        except Exception as e:
                            logger.error(f"Error processing trigger role for {member}: {e}")

        return changes
    except Exception as e:
        logger.error(f"Sync error for {guild.name}: {e}")
        return {"removed": 0, "added": 0}

async def process_role_removal(bot, guild):
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
                        bot, guild,
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

async def register_external_role_add(bot, member: discord.Member, role: discord.Role):
    """外部でロールが付与されたときに内部データを登録する（自動削除対象用）"""
    try:
        guild_id, user_id = str(member.guild.id), str(member.id)
        now_ts = now_jst().timestamp()
        if role.name in ROLES_TO_AUTO_REMOVE:
            bot.data.remove_user_setting(guild_id, user_id, role.name)
            bot.data.role_data.setdefault(guild_id, {}).setdefault(user_id, {})
            if role.name not in bot.data.role_data[guild_id][user_id]:
                bot.data.role_data[guild_id][user_id][role.name] = now_ts
                bot.data.add_role_history(guild_id, user_id, role.name, now_ts)
                await bot.data.save_all()
                logger.info(f"Registered external role add: {member.display_name} / {role.name}")
    except Exception as e:
        logger.error(f"register_external_role_add error for {member}: {e}")
