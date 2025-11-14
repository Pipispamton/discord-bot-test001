# -*- coding: utf-8 -*-
import discord
import asyncio
import logging
from config import ROLES_TO_AUTO_REMOVE
from helpers import now_jst
from core import register_external_role_add, check_and_apply_tenure_role

logger = logging.getLogger(__name__)

def setup_events(bot):
    """すべてのイベントハンドラを登録"""
    
    @bot.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        """外部でロールが付与/削除された際の検知処理。
        付与されたロールに対して即時処理（テニュアルール判定 / 自動削除ロール登録）を行う。"""
        try:
            before_roles = {r.id: r for r in before.roles}
            added = [r for r in after.roles if r.id not in before_roles]
            if not added:
                return

            guild = after.guild
            guild_id = str(guild.id)
            tenure_rules = bot.data.tenure_rules.get(guild_id, {})

            for role in added:
                # 1) もし追加ロールが自動削除対象なら内部登録（timestamp / 履歴）
                if role.name in ROLES_TO_AUTO_REMOVE:
                    asyncio.create_task(register_external_role_add(bot, after, role))

                # 2) もし追加ロールがテニュアのトリガーなら即時処理
                if role.name in tenure_rules:
                    asyncio.create_task(_handle_trigger_role_immediate(bot, after, role))
        except Exception as e:
            logger.error(f"on_member_update error for {after}: {e}")

async def _handle_trigger_role_immediate(bot, member: discord.Member, trigger_role: discord.Role):
    """トリガーロール付与検知時の即時処理ラッパー。
    check_and_apply_tenure_role を呼んでから、処理結果に応じてログ等を出す。"""
    try:
        await check_and_apply_tenure_role(bot, member, trigger_role)
        logger.info(f"Handled trigger role immediately: {member.display_name} / {trigger_role.name}")
    except Exception as e:
        logger.error(f"_handle_trigger_role_immediate error for {member}: {e}")
