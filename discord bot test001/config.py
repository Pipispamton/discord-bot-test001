# -*- coding: utf-8 -*-
from datetime import timezone, timedelta

DEBUG = False

# ファイルパス
DATA_FILE = "roles_data.json"
SETTINGS_FILE = "bot_settings_debug.json" if DEBUG else "bot_settings.json"
ROLE_HISTORY_FILE = "role_add_history.json"
LOG_CHANNEL_FILE = "log_channel_settings.json"
TENURE_RULES_FILE = "tenure_role_rules.json"
BACKUP_DIR = "backup"

# バックアップ設定
BACKUP_KEEP_GENERATIONS = 20

# タイムゾーン
JST = timezone(timedelta(hours=9))

# ポーリング間隔（秒）
CHECK_INTERVAL = 10 if DEBUG else 600
SYNC_INTERVAL = 15 if DEBUG else 3600

# バッチ処理設定
BATCH_SIZE = 20 if DEBUG else 50
API_DELAY = 0.5 if DEBUG else 0.2

# ロール設定
ROLES_TO_AUTO_REMOVE = ["注意", "警告"]
DEFAULT_REMOVE_SECONDS = {r: (15 if DEBUG else 90 * 86400) for r in ROLES_TO_AUTO_REMOVE}
