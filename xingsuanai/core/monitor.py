"""
公众号监控核心模块。
从 wechat-article-exporter 拉取各账号最新文章，
通过动态分类器归类后保存到公众号案例库。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from xingsuanai import config
from xingsuanai.core import categorizer, wechat

logger = logging.getLogger(__name__)

Callback = Callable[[str], None] | None


def _safe_filename(text: str, max_len: int = 50) -> str:
    clean = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', text)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean[:max_len]


def _load_state() -> dict:
    if config.MONITOR_STATE.exists():
        try:
            return json.loads(config.MONITOR_STATE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'seen_articles': {}, 'last_run': None}


def _save_state(state: dict) -> None:
    config.MONITOR_STATE.parent.mkdir(parents=True, exist_ok=True)
    config.MONITOR_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def run(dry_run: bool = False, progress_cb: Callback = None) -> dict:
    """
    执行一次监控扫描。

    dry_run=True 时只输出将要下载的文章，不实际下载。
    progress_cb 每条进度消息回调，供 API 层实时推送日志。
    返回 {'ok': bool, 'new_articles': int, 'accounts_checked': int, 'error': str}
    """

    def emit(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    cfg  = config.load_accounts_config()
    rate = cfg.get('rate_limit', {})
    min_acc  = rate.get('min_delay_between_accounts', 45)
    max_acc  = rate.get('max_delay_between_accounts', 120)
    min_art  = rate.get('min_delay_between_articles', 8)
    max_art  = rate.get('max_delay_between_articles', 25)
    max_new  = rate.get('max_new_articles_per_account', 3)
    max_accs = rate.get('max_accounts_per_run', 40)

    emit('检查 auth 状态...')
    if not wechat.check_auth():
        msg = 'auth 无效或过期，请运行 wechat_auth_renew.py 续期'
        emit(f'[ERROR] {msg}')
        return {'ok': False, 'error': msg, 'new_articles': 0, 'accounts_checked': 0}

    state    = _load_state()
    accounts = [a for a in cfg.get('accounts', []) if a.get('enabled', True)]
    random.shuffle(accounts)
    accounts = accounts[:max_accs]
    emit(f'本次监控 {len(accounts)} 个账号（共 {len(cfg.get("accounts", []))} 个）')

    total_new = 0
    seen      = state.setdefault('seen_articles', {})

    for i, acc in enumerate(accounts):
        name   = acc['name']
        fakeid = acc['fakeid']
        emit(f'[{i+1}/{len(accounts)}] 扫描: {name}')

        articles = wechat.get_articles(fakeid, 10)
        if not articles:
            emit(f'  {name}: 无法获取文章列表，跳过')
        else:
            new_count = 0
            seen_acc  = seen.setdefault(fakeid, [])

            for art in articles:
                link  = art.get('link', '')
                title = art.get('title', '无标题')

                if not link or link in seen_acc:
                    continue
                if new_count >= max_new:
                    break

                emit(f'  [新] {title[:50]}')

                if not dry_run:
                    # 先用标题做快速分类
                    category = categorizer.classify(title)
                    out_dir  = config.RAW_DIR / category
                    out_dir.mkdir(parents=True, exist_ok=True)

                    date_str = datetime.now().strftime('%Y%m%d')
                    fname    = f'{date_str}_{name}_{_safe_filename(title)}.md'
                    out_path = out_dir / fname

                    ok = wechat.download_article(link, out_path)
                    if ok:
                        # 下载后用全文重新分类，可能更准确
                        try:
                            content      = out_path.read_text(encoding='utf-8', errors='replace')
                            better_cat   = categorizer.classify(content)
                            if better_cat != category:
                                new_dir  = config.RAW_DIR / better_cat
                                new_dir.mkdir(parents=True, exist_ok=True)
                                new_path = new_dir / fname
                                out_path.rename(new_path)
                                emit(f'  → 重分类: {category} → {better_cat}')
                        except Exception:
                            pass

                        seen_acc.append(link)
                        if len(seen_acc) > 200:
                            seen_acc[:] = seen_acc[-200:]
                        new_count  += 1
                        total_new  += 1
                        _save_state(state)

                        if new_count < max_new:
                            t = random.uniform(min_art, max_art)
                            emit(f'  等待 {t:.0f}s...')
                            time.sleep(t)
                    else:
                        emit(f'  [WARN] 下载失败: {title[:30]}')
                else:
                    emit(f'  [DRY] → {category}/{fname}')
                    new_count += 1

        if i < len(accounts) - 1 and not dry_run:
            t = random.uniform(min_acc, max_acc)
            emit(f'  账号间等待 {t:.0f}s...')
            time.sleep(t)

    state['last_run'] = datetime.now().isoformat()
    _save_state(state)

    summary = {
        'ok': True,
        'new_articles':     total_new,
        'accounts_checked': len(accounts),
        'dry_run':          dry_run,
    }
    emit(f'监控完成：新增 {total_new} 篇文章')
    return summary
