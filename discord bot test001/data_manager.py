# -*- coding: utf-8 -*-
import json
import os
import shutil
import asyncio
import logging
from config import (
    DATA_FILE, SETTINGS_FILE, ROLE_HISTORY_FILE, LOG_CHANNEL_FILE, TENURE_RULES_FILE,
    BACKUP_DIR, BACKUP_KEEP_GENERATIONS, ROLES_TO_AUTO_REMOVE, DEFAULT_REMOVE_SECONDS, MENTION_CONFIG_FILE
)
from helpers import now_jst

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self):
        self.role_data = {}
        self.settings = {}
        self.role_add_history = {}
        self.guild_log_channels = {}
        self.tenure_rules = {}
        self.mention_config = {}
        self._lock = asyncio.Lock()
        self.load_all()

    def load_all(self):
        self.role_data = self._load_json(DATA_FILE, {})
        self.settings = self._load_json(SETTINGS_FILE, {"remove_seconds": DEFAULT_REMOVE_SECONDS.copy()})
        self.role_add_history = self._load_json(ROLE_HISTORY_FILE, {})
        self.guild_log_channels = self._load_json(LOG_CHANNEL_FILE, {})
        self.tenure_rules = self._load_json(TENURE_RULES_FILE, {})
        self.mention_config = self._load_json(MENTION_CONFIG_FILE, {})
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
            old_data = self._load_json(DATA_FILE, {})
            old_settings = self._load_json(SETTINGS_FILE, {})
            old_history = self._load_json(ROLE_HISTORY_FILE, {})
            old_log = self._load_json(LOG_CHANNEL_FILE, {})
            old_tenure = self._load_json(TENURE_RULES_FILE, {})
            old_mention = self._load_json(MENTION_CONFIG_FILE, {})
            if old_data != self.role_data or old_settings != self.settings or old_history != self.role_add_history or old_log != self.guild_log_channels or old_tenure != self.tenure_rules or old_mention != self.mention_config:
                self._backup_data()
                changed = True
            self._save_json(DATA_FILE, self.role_data)
            self._save_json(SETTINGS_FILE, self.settings)
            self._save_json(ROLE_HISTORY_FILE, self.role_add_history)
            self._save_json(LOG_CHANNEL_FILE, self.guild_log_channels)
            self._save_json(TENURE_RULES_FILE, self.tenure_rules)
            self._save_json(MENTION_CONFIG_FILE, self.mention_config)

    def _backup_data(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = now_jst().strftime("%Y%m%d_%H%M%S")
        backup_targets = [
            (DATA_FILE, f"roles_data_{ts}.json"),
            (SETTINGS_FILE, f"settings_{ts}.json"),
            (ROLE_HISTORY_FILE, f"role_history_{ts}.json"),
            (LOG_CHANNEL_FILE, f"log_channel_{ts}.json"),
            (TENURE_RULES_FILE, f"tenure_rules_{ts}.json"),
        ]
        for src, dst in backup_targets:
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(BACKUP_DIR, dst))
        self._cleanup_old_backups()

    def _cleanup_old_backups(self):
        try:
            patterns = [
                "roles_data_",
                "settings_",
                "role_history_",
                "log_channel_",
                "tenure_rules_",
            ]
            for pat in patterns:
                backups = sorted(
                    [f for f in os.listdir(BACKUP_DIR) if f.startswith(pat)],
                    reverse=True
                )
                for old_backup in backups[BACKUP_KEEP_GENERATIONS:]:
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
