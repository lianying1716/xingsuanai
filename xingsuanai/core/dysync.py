"""
抖小云 (dysync.net) API 客户端。
通过 HTTP 调用运行在 NAS 上的抖小云服务，管理抖音博主关注列表。

Token 自动管理：
- 登录后缓存 Bearer token（有效期 24h）
- 每次请求前检查是否接近过期，自动续期
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str = '') -> str:
    return os.getenv(key, default)


def _base_url() -> str:
    return _cfg('DYSYNC_URL', 'http://192.168.2.4:10101').rstrip('/')


def _username() -> str:
    return _cfg('DYSYNC_USERNAME', 'douyin')


def _password() -> str:
    return _cfg('DYSYNC_PASSWORD', '')


# ── Token 缓存 ───────────────────────────────────────────────────────────────

_token: str = ''
_token_expires_at: float = 0.0   # Unix timestamp
_RENEW_BEFORE_SECS = 3600        # 过期前 1 小时自动续期


def _login() -> str:
    """登录并返回 Bearer token，同时更新模块级缓存。"""
    global _token, _token_expires_at

    url  = f'{_base_url()}/api/auth/login'
    body = json.dumps({'username': _username(), 'password': _password()}).encode()
    req  = urllib.request.Request(url, data=body,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception as e:
        raise RuntimeError(f'抖小云登录失败: {e}') from e

    if data.get('code') != 0:
        raise RuntimeError(f'抖小云登录失败: {data.get("msg", data)}')

    _token           = data['token']
    expires_ms       = data.get('expires', 86400000)   # 毫秒
    _token_expires_at = time.time() + expires_ms / 1000
    logger.info('抖小云 token 获取成功，有效期 %.0fh', expires_ms / 3600000)
    return _token


def _get_token() -> str:
    """返回有效 token，必要时自动续期。"""
    if not _token or time.time() >= _token_expires_at - _RENEW_BEFORE_SECS:
        _login()
    return _token


# ── 通用请求 ─────────────────────────────────────────────────────────────────

def _request(path: str, method: str = 'GET', body: Any = None) -> dict:
    url     = f'{_base_url()}{path}'
    data    = json.dumps(body).encode() if body is not None else None
    headers = {
        'Authorization': f'Bearer {_get_token()}',
        'Accept':        'application/json',
    }
    if data:
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Token 失效，强制重新登录后重试一次
            _login()
            req = urllib.request.Request(url, data=data,
                                         headers={**headers, 'Authorization': f'Bearer {_token}'},
                                         method=method)
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.load(r)
        else:
            raise RuntimeError(f'抖小云 API {path} 失败: HTTP {e.code}') from e
    except Exception as e:
        raise RuntimeError(f'抖小云 API {path} 失败: {e}') from e

    return resp


# ── 公共 API ─────────────────────────────────────────────────────────────────

def get_accounts() -> list[dict]:
    """返回登录的抖音账号列表（mySelfId / name / total）。"""
    resp = _request('/api/config/list')
    return resp.get('data') or []


def get_bloggers(page: int = 0, page_size: int = 50,
                 my_self_id: str = '', name_filter: str | None = None) -> dict:
    """
    分页获取关注博主列表。
    返回 {total, data: [{id, uperName, uperId, secUid, openSync, fullSync, savePath,
                         isNoFollowed, douyinNo, signature, uperAvatar}]}
    """
    if not my_self_id:
        accounts = get_accounts()
        my_self_id = accounts[0]['key'] if accounts else ''

    resp = _request('/api/follow/paged', method='POST', body={
        'pageIndex':      page,
        'pageSize':       page_size,
        'followUserName': name_filter,
        'mySelfId':       my_self_id,
    })
    data = resp.get('data') or {}
    return {
        'total':      data.get('total', 0),
        'my_self_id': my_self_id,
        'bloggers':   data.get('data') or [],
    }


def toggle_blogger_sync(record_id: str, open_sync: bool,
                        full_sync: bool = False, save_path: str = '',
                        uper_id: str = '') -> dict:
    """开关某博主的视频同步。"""
    resp = _request('/api/follow/openOrCloseSync', method='POST', body={
        'Id':       record_id,
        'OpenSync': open_sync,
        'FullSync': full_sync,
        'SavePath': save_path,
        'uperId':   uper_id,
    })
    if resp.get('code') != 0:
        raise RuntimeError(f"toggle_sync 失败: {resp.get('msg', resp)}")
    return {'ok': True, 'id': record_id, 'openSync': open_sync, 'fullSync': full_sync}


def add_blogger(uper_name: str, uper_id: str, sec_uid: str,
                save_path: str = '', open_sync: bool = False,
                full_sync: bool = False, my_self_id: str = '') -> dict:
    """
    添加新博主到监控列表。
    uperId / secUid 从抖音主页 URL 中获取。
    """
    if not my_self_id:
        accounts = get_accounts()
        my_self_id = accounts[0]['key'] if accounts else ''

    resp = _request('/api/follow/add', method='POST', body={
        'mySelfId':    my_self_id,
        'uperName':    uper_name,
        'uperId':      uper_id,
        'secUid':      sec_uid,
        'savePath':    save_path or uper_name,
        'openSync':    open_sync,
        'fullSync':    full_sync,
        'signature':   '',
        'uperAvatar':  '',
        'enterprise':  '',
        'isNoFollowed': True,
    })
    if resp.get('code') != 0:
        raise RuntimeError(f"add_blogger 失败: {resp.get('msg', resp)}")
    return {'ok': True, 'name': uper_name, 'uperId': uper_id}


def delete_blogger(record_id: str, my_self_id: str = '', uper_id: str = '') -> dict:
    """从监控列表删除博主（仅限 isNoFollowed=True 的手动添加博主）。"""
    if not my_self_id:
        accounts = get_accounts()
        my_self_id = accounts[0]['key'] if accounts else ''

    resp = _request('/api/follow/delete', method='POST', body={
        'id':       record_id,
        'mySelfId': my_self_id,
        'uperId':   uper_id,
    })
    if resp.get('code') != 0:
        raise RuntimeError(f"delete_blogger 失败: {resp.get('msg', resp)}")
    return {'ok': True, 'deleted_id': record_id}


def trigger_sync() -> dict:
    """立即触发一次全部博主的视频同步。"""
    resp = _request('/api/follow/sync')
    return {'ok': resp.get('code') == 0, 'msg': resp.get('msg', '')}


def get_stats() -> dict:
    """返回抖小云统计数据（视频数、博主数、分类数、磁盘占用）。"""
    resp = _request('/api/video/statics')
    data = resp.get('data') or {}
    return {
        'video_count':      data.get('videoCount', 0),
        'author_count':     data.get('authorCount', 0),
        'category_count':   data.get('categoryCount', 0),
        'total_size_gb':    data.get('videoSizeTotal', '0'),
        'follow_size_gb':   data.get('videoFollowSize', '0'),
        'collect_size_gb':  data.get('videoCollectSize', '0'),
        'top_authors':      [
            {'name': a.get('name'), 'count': a.get('count'), 'uperId': a.get('uperId')}
            for a in (data.get('authors') or [])[:10]
        ],
    }


def check_connection() -> dict:
    """检查抖小云连通性和 token 有效性。"""
    try:
        accounts = get_accounts()
        return {
            'ok':       True,
            'url':      _base_url(),
            'accounts': [{'name': a.get('name'), 'key': a.get('key')} for a in accounts],
        }
    except Exception as e:
        return {'ok': False, 'url': _base_url(), 'error': str(e)}
