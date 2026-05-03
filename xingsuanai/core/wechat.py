"""
wechat-article-exporter CLI 封装。
容器内通过 subprocess 调用挂载进来的 wechat_article.py。
不走 SSH，不留脚本——调用发生在容器内部。
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from xingsuanai.config import WECHAT_SCRIPT, WECHAT_PY, get_wechat_env

logger = logging.getLogger(__name__)


def run(args: list[str], timeout: int = 90) -> dict | None:
    env = get_wechat_env()
    full_env = os.environ.copy()
    full_env.update(env)
    cmd = [WECHAT_PY, str(WECHAT_SCRIPT)] + args
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            env=full_env,
            timeout=timeout,
            cwd=str(WECHAT_SCRIPT.parent),
        )
        text = r.stdout.decode('utf-8', errors='replace').strip()
        if not text:
            if r.stderr:
                logger.warning('wechat stderr: %s', r.stderr.decode('utf-8', errors='replace')[:200])
            return None
        return json.loads(text)
    except subprocess.TimeoutExpired:
        logger.warning('wechat CLI timeout (%ds): %s', timeout, args[:2])
        return None
    except json.JSONDecodeError as e:
        logger.warning('wechat CLI bad JSON: %s', e)
        return None
    except Exception as e:
        logger.error('wechat CLI error: %s', e)
        return None


def check_auth() -> bool:
    result = run(['ensure-auth'])
    return bool(result and result.get('ok'))


def get_articles(fakeid: str, limit: int = 10) -> list[dict]:
    result = run(['search-articles', '--fakeid', fakeid, '--limit', str(limit)])
    if result:
        return result.get('articles', [])
    return []


def download_article(url: str, output_path: Path) -> bool:
    run(['download-article', '--url', url, '--format', 'markdown',
         '--output', str(output_path)], timeout=120)
    return output_path.exists() and output_path.stat().st_size > 100


def search_accounts(keyword: str) -> list[dict]:
    result = run(['search-accounts', keyword])
    if result:
        return result.get('accounts', result.get('list', []))
    return []


def discover_accounts(keyword: str, size: int = 10, begin: int = 0) -> list[dict]:
    """通过 wechat-article-exporter /api/web/mp/searchbiz 搜索公众号。
    直接调 HTTP，无需经过 CLI 脚本。
    """
    import urllib.request
    env = get_wechat_env()
    base_url = env.get('WECHAT_ARTICLE_BASE_URL', 'http://localhost:7799')
    auth_key = env.get('WECHAT_ARTICLE_AUTH_KEY', '')

    url = (
        f'{base_url}/api/web/mp/searchbiz'
        f'?keyword={urllib.request.quote(keyword)}'
        f'&size={size}&begin={begin}'
    )
    req = urllib.request.Request(url, headers={'X-Auth-Key': auth_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        base = data.get('base_resp', {})
        if base.get('ret') != 0:
            logger.warning('searchbiz error: %s', base.get('err_msg'))
            return []
        return data.get('list', [])
    except Exception as e:
        logger.error('discover_accounts failed: %s', e)
        return []
