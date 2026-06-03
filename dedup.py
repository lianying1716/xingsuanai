#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
去重共享模块 —— 所有导入脚本(github_import / clawhub_import / group_mine)统一调用
============================================================
在「state 去重 + 数据库 upsert 幂等」之外,再加两道防线:
  ① canonical 仓库指纹:把任意 GitHub 链接/owner-repo 归一化成 owner/repo(小写),
     用于跨源(GitHub↔ClawHub)、换名前缀、tree 子路径 的强去重(零误判,因为是同一个仓)。
  ② core_name 模糊指纹:剥掉 -skill/-mcp/-plugin 等包装后缀,用于"疑似重复"巡检报告
     (只报告给人,不自动删,避免误伤同主题的不同项目)。
内置 build_live_index():一次性拉客户端真实接口,建好线上已存在的指纹索引。
"""
import json, re, urllib.request

# 仅作为"包装后缀"剥离,绝不剥主题词(如 seo/translate)。可按需增减。
_PKG_SUFFIX = {
    "skill", "skills", "mcp", "mcpserver", "plugin", "plugins", "pack", "packs",
    "agent", "agents", "official", "awesome", "cli", "app", "tool", "tools",
    "claude", "cursor", "codex", "openclaw", "ai", "demo", "example", "examples",
    "main", "master", "starter", "template", "templates",
}


def canon_repo(s):
    """任意 GitHub 链接 / 'owner/repo' / 'owner/repo/tree/...' → 'owner/repo'(小写)。非 GitHub 仓返回 ''。"""
    if not s:
        return ""
    s = str(s).strip().lower()
    if s.startswith("clawhub:") or s.startswith("clawhub-"):
        return ""
    s = re.sub(r"^https?://(www\.)?github\.com/", "", s)
    s = re.sub(r"\.git$", "", s).strip("/")
    parts = s.split("/")
    if len(parts) >= 2 and parts[0] and parts[1] and "." not in parts[0]:
        return f"{parts[0]}/{parts[1]}"
    return ""


def core_name(name):
    """模糊主名:取最后一段 → 拆词 → 反复剥包装后缀/前缀 → 拼回。仅用于巡检聚类。"""
    if not name:
        return ""
    n = str(name).strip().lower()
    n = re.sub(r"^https?://(www\.)?github\.com/", "", n)
    n = re.sub(r"^clawhub[:\-]", "", n)
    n = n.rstrip("/").split("/")[-1]
    n = re.sub(r"\.git$", "", n)
    toks = [t for t in re.split(r"[^a-z0-9]+", n) if t]
    # 反复剥两端的包装词
    changed = True
    while changed and len(toks) > 1:
        changed = False
        if toks[-1] in _PKG_SUFFIX:
            toks.pop(); changed = True
        if len(toks) > 1 and toks[0] in _PKG_SUFFIX:
            toks.pop(0); changed = True
    return "".join(toks)


def build_live_index(get_json):
    """get_json(path)->dict;拉客户端真实接口,返回去重索引。"""
    idx = {"repos": set(), "ids": set(), "cores": {}}
    try:
        plugins = (get_json("/api/desktop/v1/plugins") or {}).get("items", []) or []
    except Exception:
        plugins = []
    try:
        cards = (get_json("/api/content/github-trending?page_size=200") or {}).get("items", []) or []
    except Exception:
        cards = []

    def add_core(key, kind, label):
        if key:
            idx["cores"].setdefault(key, []).append((kind, label))

    for p in plugins:
        pid = str(p.get("plugin_id") or "")
        idx["ids"].add(pid)
        up = p.get("upstream_id") or ""
        cr = canon_repo(up) or canon_repo(p.get("source_url"))
        if cr:
            idx["repos"].add(cr)
        add_core(core_name(cr or up or pid), "插件", pid)
    for c in cards:
        m = c.get("metadata") or {}
        sid = m.get("repoFullName") or c.get("source_id") or ""
        idx["ids"].add(str(sid))
        cr = canon_repo(c.get("url")) or canon_repo(sid)
        if cr:
            idx["repos"].add(cr)
        add_core(core_name(cr or c.get("url") or sid), "内容卡", cr or sid)
    return idx


def repo_exists(idx, owner_repo):
    """该 GitHub 仓是否已在客户端(任意中心)。owner_repo 可带或不带 github.com 前缀。"""
    return canon_repo(owner_repo) in idx["repos"]
