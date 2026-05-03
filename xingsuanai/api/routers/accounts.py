"""
/accounts 路由 — 管理监控账号列表，搜索新账号。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from xingsuanai import config
from xingsuanai.core import wechat

router = APIRouter()


class AddAccountRequest(BaseModel):
    name:     str
    fakeid:   str
    keywords: list[str] = []


class SearchRequest(BaseModel):
    keyword: str


class ToggleRequest(BaseModel):
    enabled: bool


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get('', summary='列出所有监控账号')
def list_accounts():
    cfg = config.load_accounts_config()
    accounts = cfg.get('accounts', [])
    enabled  = sum(1 for a in accounts if a.get('enabled', True))
    return {
        'total':   len(accounts),
        'enabled': enabled,
        'accounts': accounts,
    }


@router.post('/search', summary='搜索新账号（通过 wechat-article-exporter）')
def search_accounts(req: SearchRequest):
    results = wechat.search_accounts(req.keyword)
    return {'keyword': req.keyword, 'results': results, 'count': len(results)}


@router.post('', summary='添加新账号到监控列表')
def add_account(req: AddAccountRequest):
    cfg = config.load_accounts_config()
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
