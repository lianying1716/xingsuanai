"""
/materials 路由 — 浏览素材卡片、获取分类列表、查询统计。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from xingsuanai import config
from xingsuanai.core import processor

router = APIRouter()


@router.get('', summary='列出素材卡片')
def list_materials(
    grade:    str | None = Query(None, description='按等级过滤，如 A / B / C / D'),
    category: str | None = Query(None, description='按分类名称过滤（部分匹配）'),
    q:        str | None = Query(None, description='关键词搜索标题或工具名'),
    limit:    int        = Query(50,   description='最多返回条数'),
    offset:   int        = Query(0,    description='分页偏移'),
):
    state = {}
    if config.PROCESS_STATE.exists():
        try:
            state = json.loads(config.PROCESS_STATE.read_text(encoding='utf-8'))
        except Exception:
            pass

    entries = list(state.get('processed', {}).values())

    if grade:
        entries = [e for e in entries if e.get('grade', '').upper() == grade.upper()]
    if category:
        entries = [e for e in entries if category in e.get('category', '')]
    if q:
        q_lower = q.lower()
        entries = [e for e in entries
                   if q_lower in e.get('title', '').lower()
                   or q_lower in e.get('tools', '').lower()
                   or q_lower in e.get('account', '').lower()]

    entries.sort(key=lambda x: x.get('date', ''), reverse=True)
    total = len(entries)
    return {
        'total':  total,
        'offset': offset,
        'limit':  limit,
        'items':  entries[offset:offset + limit],
    }


@router.get('/stats', summary='素材库统计')
def get_stats():
    return processor.get_stats()


@router.get('/categories', summary='已有分类列表（从目录结构动态读取）')
def list_categories():
    cats = []
    if config.RAW_DIR.exists():
        cats = sorted(d.name for d in config.RAW_DIR.iterdir() if d.is_dir())
    return {'categories': cats, 'total': len(cats)}


@router.get('/{filename}', summary='读取单篇卡片全文')
def get_material(filename: str):
    if not config.CARD_DIR.exists():
        raise HTTPException(404, '素材库目录不存在')

    for cat_dir in config.CARD_DIR.iterdir():
        if not cat_dir.is_dir():
            continue
        f = cat_dir / filename
        if f.exists():
            return {
                'filename': filename,
                'category': cat_dir.name,
                'content':  f.read_text(encoding='utf-8'),
            }

    raise HTTPException(404, f'卡片文件 {filename} 不存在')
