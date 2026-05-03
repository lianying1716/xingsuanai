"""
/accounts 路由 — 管理监控账号列表，搜索/发现新账号。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from xingsuanai import config
from xingsuanai.core import wechat

router = APIRouter()


class AddAccountRequest(BaseModel):
    name:     str
    fakeid:   str
    keywords: list[str] = []


class BatchAddRequest(BaseModel):
    accounts: list[AddAccountRequest]


class ToggleRequest(BaseModel):
    enabled: bool


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get('', summary='列出所有监控账号')
def list_accounts():
    cfg      = config.load_accounts_config()
    accounts = cfg.get('accounts', [])
    enabled  = sum(1 for a in accounts if a.get('enabled', True))
    return {
        'total':    len(accounts),
        'enabled':  enabled,
        'accounts': accounts,
    }


@router.get('/discover', summary='关键词搜索公众号（用于发现新账号）')
def discover_accounts(
    keywords: str = Query(..., description='搜索关键词，多词用空格分隔'),
    size:     int = Query(10, ge=1, le=20, description='返回数量'),
    begin:    int = Query(0,  ge=0, description='翻页偏移'),
):
    """
    通过 wechat-article-exporter searchbiz 接口按关键词搜索公众号。
    返回的每条记录已标注 `already_monitored` 字段，方便判断是否已在监控列表中。
    """
    candidates = wechat.discover_accounts(keywords, size=size, begin=begin)
    if not candidates:
        return {'keywords': keywords, 'results': [], 'count': 0}

    cfg            = config.load_accounts_config()
    monitored_ids  = {a['fakeid'] for a in cfg.get('accounts', [])}

    results = []
    for c in candidates:
        results.append({
            'fakeid':            c.get('fakeid', ''),
            'name':              c.get('nickname', ''),
            'alias':             c.get('alias', ''),
            'signature':         c.get('signature', ''),
            'avatar':            c.get('round_head_img', ''),
            'verified':          c.get('verify_status', 0) == 1,
            'already_monitored': c.get('fakeid', '') in monitored_ids,
        })

    return {'keywords': keywords, 'results': results, 'count': len(results)}


@router.post('/batch', summary='批量添加账号到监控列表')
def batch_add_accounts(req: BatchAddRequest):
    """
    批量添加账号。已存在的自动跳过（不报错）。
    配合 /accounts/discover 使用：搜索 → 选择 → 批量添加。
    """
    cfg      = config.load_accounts_config()
    accounts = cfg.setdefault('accounts', [])
    existing = {a['fakeid'] for a in accounts}

    added   = []
    skipped = []
    for item in req.accounts:
        if item.fakeid in existing:
            skipped.append(item.name)
            continue
        accounts.append({
            'name':     item.name,
            'fakeid':   item.fakeid,
            'keywords': item.keywords,
            'enabled':  True,
        })
        existing.add(item.fakeid)
        added.append(item.name)

    if added:
        _write_config(cfg)

    return {
        'ok':      True,
        'added':   added,
        'skipped': skipped,
        'total':   len(accounts),
    }


@router.post('/reload', summary='热重载账号配置（无需重启容器）')
def reload_config():
    cfg = config.load_accounts_config()
    return {
        'ok':    True,
        'total': len(cfg.get('accounts', [])),
        'hint':  '配置已重新读取，下次监控任务生效',
    }


@router.post('', summary='添加单个账号到监控列表')
def add_account(req: AddAccountRequest):
    cfg      = config.load_accounts_config()
    accounts = cfg.setdefault('accounts', [])

    if any(a['fakeid'] == req.fakeid for a in accounts):
        raise HTTPException(400, f'账号 {req.fakeid} 已存在')

    accounts.append({
        'name':     req.name,
        'fakeid':   req.fakeid,
        'keywords': req.keywords,
        'enabled':  True,
    })
    _write_config(cfg)
    return {'ok': True, 'added': req.name, 'total': len(accounts)}


@router.patch('/{fakeid}', summary='启用/禁用账号')
def toggle_account(fakeid: str, req: ToggleRequest):
    cfg      = config.load_accounts_config()
    accounts = cfg.get('accounts', [])
    for a in accounts:
        if a['fakeid'] == fakeid:
            a['enabled'] = req.enabled
            _write_config(cfg)
            return {'ok': True, 'fakeid': fakeid, 'enabled': req.enabled}
    raise HTTPException(404, f'账号 {fakeid} 不存在')


@router.delete('/{fakeid}', summary='删除账号')
def delete_account(fakeid: str):
    cfg      = config.load_accounts_config()
    accounts = cfg.get('accounts', [])
    before   = len(accounts)
    cfg['accounts'] = [a for a in accounts if a['fakeid'] != fakeid]
    if len(cfg['accounts']) == before:
        raise HTTPException(404, f'账号 {fakeid} 不存在')
    _write_config(cfg)
    return {'ok': True, 'deleted': fakeid}


@router.get('/auth/status', summary='检查 wechat auth 是否有效')
def auth_status():
    ok = wechat.check_auth()
    return {
        'ok':    ok,
        'valid': ok,
        'hint':  '' if ok else '请在 NAS 上运行 python3 wechat_auth_renew.py 扫码续期',
    }


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _write_config(cfg: dict) -> None:
    config.ACCOUNTS_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8'
    )
