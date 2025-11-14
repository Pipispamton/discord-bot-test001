# -*- coding: utf-8 -*-
from datetime import datetime
from config import JST

def now_jst():
    """現在時刻（JST）を取得"""
    return datetime.now(JST)

def timestamp_to_jst(ts):
    """タイムスタンプを JST の datetime に変換"""
    return datetime.fromtimestamp(ts, JST)

def format_duration(seconds: float) -> str:
    """秒数を人間が読みやすい形式に変換"""
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

def parse_duration(days=0, hours=0, minutes=0, seconds=0) -> int:
    """日時分秒を秒数に変換"""
    return int(days) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)

def is_valid_guild_data(guild_id: str) -> bool:
    """ギルドID形式が有効か確認"""
    try:
        if not guild_id or not isinstance(guild_id, str):
            return False
        int(guild_id)
        return True
    except (ValueError, TypeError):
        return False

def validate_role_data(data: dict) -> bool:
    """ロールデータの整合性を確認"""
    import logging
    logger = logging.getLogger(__name__)
    try:
        if not isinstance(data, dict):
            return False
        for guild_id, guild_data in data.items():
            if not is_valid_guild_data(guild_id):
                logger.warning(f"Invalid guild_id format: {guild_id}")
                return False
            if not isinstance(guild_data, dict):
                return False
            for user_id, user_roles in guild_data.items():
                if not isinstance(user_id, str) or not user_id.isdigit():
                    logger.warning(f"Invalid user_id format: {user_id}")
                    return False
                if not isinstance(user_roles, dict):
                    return False
                for role_name, timestamp in user_roles.items():
                    if not isinstance(role_name, str):
                        return False
                    if not isinstance(timestamp, (int, float)):
                        logger.warning(f"Invalid timestamp for {role_name}: {timestamp}")
                        return False
        return True
    except Exception as e:
        logger.error(f"Role data validation error: {e}")
        return False
