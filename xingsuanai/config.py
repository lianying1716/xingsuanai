"""
统一配置模块 — 所有路径、环境变量从这里读取。
路径以 Docker 挂载约定为准，本地开发可通过 XINGSUANAI_DATA_DIR 覆盖。
"""

import json
import os
from pathlib import Path

# ── 数据目录 ──────────────────────────────────────────────────────────────────
# Docker: /data/素材库/星算运营库  (volume: /vol1/1000/素材库 → /data/素材库)
BASE_DIR   = Path(os.getenv('XINGSUANAI_DATA_DIR', '/data/素材库/星算运营库'))
RAW_DIR    = BASE_DIR / '素材库/公众号案例库'
CARD_DIR   = BASE_DIR / '素材库/内容素材库/公众号素材'
INDEX_FILE = CARD_DIR / 'INDEX.md'
LOG_DIR    = BASE_DIR / 'logs'

ACCOUNTS_FILE  = BASE_DIR / 'wechat_monitor_config.json'
MONITOR_STATE  = BASE_DIR / 'wechat_monitor_state.json'
PROCESS_STATE  = CARD_DIR / '.processed.json'

# ── wechat-article-exporter CLI ───────────────────────────────────────────────
# 宿主机 wechat-skill 目录挂载到容器 /opt/wechat-skill
WECHAT_SCRIPT = Path(os.getenv('WECHAT_SCRIPT_PATH', '/opt/wechat-skill/wechat_article.py'))
WECHAT_PY     = os.getenv('WECHAT_VENV_PY', '/opt/wechat-venv/bin/python3')


def load_accounts_config() -> dict:
    if ACCOUNTS_FILE.exists():
        try:
            return json.loads(ACCOUNTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def get_wechat_env() -> dict:
    """返回调用 wechat-article CLI 所需的环境变量，配置文件优先，Docker env 兜底。"""
    cfg_env = load_accounts_config().get('env', {})
    def _get(key: str) -> str:
        return cfg_env.get(key) or os.getenv(key, '')

    return {
        'WECHAT_ARTICLE_BASE_URL':   _get('WECHAT_ARTICLE_BASE_URL')   or 'http://localhost:7799',
        'WECHAT_ARTICLE_AUTH_KEY':   _get('WECHAT_ARTICLE_AUTH_KEY'),
        'WECHAT_ARTICLE_PROXY_URLS': _get('WECHAT_ARTICLE_PROXY_URLS'),
        'WECHAT_ARTICLE_PROXY_AUTH': _get('WECHAT_ARTICLE_PROXY_AUTH'),
    }
