#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 内容来源模块 · P0.5 完整双线 (路线 Y：我方加工后直发)
================================================================
一条发现线 → 自动分流 → 两个出口：
  • skill/插件类(有 SKILL.md/MCP) + license 合规 → 插件中心(desktop:plugins:save, draft)
  • 其余普通热门/冷门项目(及 license 不合规的 skill) → 内容中心 GitHub 热门卡(upsertGithubTrending)

关键区别：
  - 插件中心 = 再分发代码 → 受 license 白名单门限制(只收 MIT/Apache/BSD…)
  - 内容卡   = 仅"大白话介绍 + 链接"(编辑性) → 不受 license 限制，任何项目都能做引流

用法：
  python3 github_import.py --discover                 # 挖候选并打印分流去向(不写库)
  python3 github_import.py --discover --apply         # 挖到的逐个加工并入库(自动分流)
  python3 github_import.py --add owner/repo           # 单仓加工(dry-run，自动判定去向)
  python3 github_import.py --add owner/repo --apply   # 单仓加工并入库
  选项：--limit N(默认 10) --no-ai --publish(插件直接上架) --visible(内容卡直接展示)
       --allow-any-license(强制把非白名单 skill 也塞进插件中心，标警告)

安全门：插件一律 draft(--publish 才发布)；内容卡默认 is_visible=false(--visible 才展示)；
        SKILL.md 危险命令静态扫描；保留 source_url + upstream_id 署名。
依赖：标准库 only。凭证读同目录 .env。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import dedup  # 共享去重模块

ENV_PATH = Path(__file__).resolve().parent / ".env"

LIVE_IDX = None  # 运行前用 build_live_index 填充的线上指纹索引(跨源去重防线)


def load_env():
    cfg = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    for k in ("GITHUB_TOKEN", "NEXAI_RELAY_BASE_URL", "NEXAI_RELAY_API_KEY",
              "NEXAI_RELAY_MODEL", "OPERATOR_BASE_URL", "OPERATOR_TOKEN"):
        cfg.setdefault(k, os.environ.get(k, ""))
    return cfg


CFG = load_env()
GITHUB_TOKEN = CFG.get("GITHUB_TOKEN", "")
RELAY_BASE = CFG.get("NEXAI_RELAY_BASE_URL", "https://api.xsai5.xyz/v1").rstrip("/")
RELAY_KEY = CFG.get("NEXAI_RELAY_API_KEY", "")
RELAY_MODEL = CFG.get("NEXAI_RELAY_MODEL", "gpt-5.4-mini")
OP_BASE = CFG.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/")
OP_TOKEN = CFG.get("OPERATOR_TOKEN", "")

LICENSE_WHITELIST = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense"}
SKILL_MAX = 8  # 一个仓超过这么多 skill 视为"技能合集仓"，不逐个入插件，改做引流内容卡
SKILL_TOPICS = {"claude-code", "claude-skill", "claude-skills", "agent-skill",
                "claude-code-plugin", "mcp", "mcp-server", "cursor-rules", "codex"}
# 发现：既挖 skill 类也挖普通热门/冷门项目(面向我们受众)
DISCOVER_QUERIES = [
    "claude code OR claude skill OR mcp stars:120..3000 created:>2026-02-01 pushed:>2026-05-15",
    "ai tool OR cli OR agent stars:150..3000 created:>2026-03-01 pushed:>2026-05-18",
    "developer productivity OR automation stars:100..2500 created:>2026-02-15 pushed:>2026-05-18",
]
NOW = datetime.now(timezone.utc)

# 动态分类配置(外部可直接编辑 github_categories.json 增删分类/标签，无需改代码)
CAT_PATH = ENV_PATH.parent / "github_categories.json"
_DEFAULT_CATS = {"categories": [{"name": "其他", "emoji": "📦", "desc": "兜底"}], "tags": []}


def load_categories():
    if CAT_PATH.exists():
        try:
            d = json.loads(CAT_PATH.read_text(encoding="utf-8"))
            if d.get("categories"):
                return d
        except Exception as e:
            print(f"[cats] 读取分类配置失败，用兜底: {e}", file=sys.stderr)
    return _DEFAULT_CATS


CATS = load_categories()
CAT_NAMES = [c["name"] for c in CATS.get("categories", [])]
TAG_VOCAB = CATS.get("tags", [])


def _req(url, headers=None, data=None, method=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def gh(url):
    h = {"User-Agent": "xsai-content-pipeline", "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    st, body = _req(url, h)
    if st != 200:
        raise RuntimeError(f"GitHub API {st}: {body[:160]}")
    return json.loads(body)


def operator(path, data=None, method=None):
    st, body = _req(OP_BASE + path, {"x-operator-token": OP_TOKEN}, data, method)
    try:
        return st, json.loads(body)
    except Exception:
        return st, {"_raw": body[:200]}


def client_get(path):
    """拉客户端公开接口(无需 token),供去重索引用。"""
    st, body = _req(OP_BASE + path, {"User-Agent": "xsai-content-pipeline"})
    try:
        return json.loads(body) if st == 200 else {}
    except Exception:
        return {}


def build_live_index():
    global LIVE_IDX
    LIVE_IDX = dedup.build_live_index(client_get)
    print(f"[dedup] 线上索引:{len(LIVE_IDX['repos'])} 个仓 / {len(LIVE_IDX['ids'])} 个ID(跨源去重已就绪)")


STATE_PATH = ENV_PATH.parent / ".github_state.json"


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed": {}}


def save_state(s):
    try:
        STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[state] 写入失败: {e}", file=sys.stderr)


def slugify_skill(s):
    return ("skill-" + re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-"))[:64]


def license_ok(spdx):
    return spdx in LICENSE_WHITELIST


def scan_risky(text):
    pats = [r"rm\s+-rf", r"curl\s+[^|]*\|\s*(ba)?sh", r"wget\s+[^|]*\|\s*(ba)?sh",
            r"eval\s*\(", r"base64\s+-d", r"chmod\s+777", r"/etc/passwd", r"sudo\s"]
    return [p for p in pats if re.search(p, text or "", re.I)]


def fetch_raw(owner, repo, branch, path):
    try:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        return urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "x"}), timeout=30).read().decode()
    except Exception:
        return ""


def _chat(prompt, max_tokens=700, retries=2):
    for attempt in range(retries + 1):
        st, body = _req(f"{RELAY_BASE}/chat/completions",
                        {"Authorization": f"Bearer {RELAY_KEY}"},
                        {"model": RELAY_MODEL, "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0.3, "max_tokens": max_tokens})
        if st == 200:
            try:
                raw = json.loads(body)["choices"][0]["message"]["content"].strip()
                raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
                return json.loads(raw)
            except Exception:
                print(f"[ai] 返回非 JSON，重试({attempt + 1}/{retries})", file=sys.stderr)
        else:
            print(f"[ai] 模型 {st}，重试({attempt + 1}/{retries})", file=sys.stderr)
        if attempt < retries:
            time.sleep(3)
    return None


# ── 发现 ──────────────────────────────────────────────────────────────
def discover(limit):
    seen = {}
    for q in DISCOVER_QUERIES:
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 20})
        try:
            data = gh(url)
        except Exception as e:
            print(f"[discover] 查询失败跳过: {e}", file=sys.stderr)
            time.sleep(6)
            continue
        for it in data.get("items", []):
            fn = it["full_name"]
            if fn in seen:
                continue
            created = datetime.fromisoformat(it["created_at"].replace("Z", "+00:00"))
            age = max((NOW - created).days, 1)
            stars, forks = it["stargazers_count"], it["forks_count"]
            topics = it.get("topics", []) or []
            seen[fn] = {
                "repo": fn, "stars": stars, "forks": forks,
                "vel": round(stars / age, 1),
                "fs": round(forks / stars, 3) if stars else 0,
                "is_skill_topic": bool(set(topics) & SKILL_TOPICS),
                "license": (it.get("license") or {}).get("spdx_id"),
                "desc": (it.get("description") or "")[:120],
                "topics": topics[:8], "default_branch": it.get("default_branch", "main"),
                "language": it.get("language"),
            }
        time.sleep(6)
    rows = list(seen.values())
    rows.sort(key=lambda x: x["vel"], reverse=True)
    return rows[:limit]


# ── 仓库探测 ──────────────────────────────────────────────────────────
def fetch_meta(owner, repo):
    d = gh(f"https://api.github.com/repos/{owner}/{repo}")
    return {
        "branch": d.get("default_branch", "main"),
        "stars": d.get("stargazers_count", 0), "forks": d.get("forks_count", 0),
        "description": d.get("description"), "topics": d.get("topics", []),
        "license": (d.get("license") or {}).get("spdx_id"), "language": d.get("language"),
    }


def fetch_contributors(owner, repo):
    """取真实贡献者数(用 Link 头的 last 页号；失败返回 0，不造假)。"""
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=1&anon=1"
        h = {"User-Agent": "xsai-content-pipeline", "Accept": "application/vnd.github+json"}
        if GITHUB_TOKEN:
            h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=30) as r:
            link = r.getheader("Link") or ""
            data = json.loads(r.read().decode())
        m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', link)
        if m:
            return int(m.group(1))
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def find_skill_md(owner, repo, branch, include_dot=False):
    tree = gh(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    blobs = [e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"]
    skill_mds = [p for p in blobs if p == "SKILL.md" or p.endswith("/SKILL.md")]
    # 忽略 dot 目录(.agents/.claude/.cursor/.codex/.github 等)下的 SKILL.md：
    # 那是项目「自己开发用」的 AI 配置，不是给终端用户安装的产品级 skill。
    if not include_dot:
        skill_mds = [p for p in skill_mds if not any(seg.startswith(".") for seg in p.split("/")[:-1])]
    dirs = [("" if p == "SKILL.md" else p[:-len("/SKILL.md")]) for p in skill_mds]
    out = []
    for d in dirs:
        prefix = (d + "/") if d else ""
        deeper = [x for x in dirs if x != d and x.startswith(prefix) and len(x) > len(d)]
        files = [p[len(prefix):] for p in blobs
                 if p.startswith(prefix) and len(p) > len(prefix)
                 and not any(p.startswith(dd + "/") for dd in deeper)]
        out.append((d, d.split("/")[-1] if d else repo, files))
    return out


# ── 加工 ──────────────────────────────────────────────────────────────
def ai_skill(skill_md):
    if not (RELAY_KEY and RELAY_MODEL):
        return None
    return _chat(
        "你是给「AI 工具小白」写说明的编辑。下面是一个 Claude Code/Cursor/Codex 技能的 SKILL.md。\n"
        "用最通俗的简体中文(英文缩写要解释)，输出严格 JSON：display_name_zh(8字内), "
        "summary_zh(一句话干嘛的), description_zh(2-3句:能帮你做什么+适合谁), "
        "usage_zh(装好后怎么用,1-2句), category(从[内容创作,开发工具,效率工具,设计,其他]选一)。只输出 JSON。\n"
        f"---\n{skill_md[:5000]}\n---")


def ai_card(name, desc, readme):
    if not (RELAY_KEY and RELAY_MODEL):
        return None
    cat_guide = "；".join(f"{c['name']}({c.get('desc', '')})" for c in CATS.get("categories", []))
    return _chat(
        "你是给「AI 工具小白」写 GitHub 项目介绍卡的编辑。读项目信息，用最通俗的简体中文"
        "(英文缩写/术语必须解释，多用生活类比，禁居高临下)，输出严格 JSON：\n"
        "display_name_zh(12字内中文名), "
        "summary_zh(一句话说清这是个啥 + 为什么有意思/亮点，要能勾起兴趣), "
        "use_cases_zh(2-3个普通人能用上的具体场景，用、分隔), "
        "usage_zh(拿到后怎么用，1-2句白话), "
        "install_zh(★从 README 原样提取真实的安装或运行命令，如 npm install x / pip install x / "
        "npx x / docker run ... / uv add x；多条用换行分隔；README 没有明确命令就返回空字符串), "
        f"category(必须从这些里选一个最贴切的，只填名字：{'/'.join(CAT_NAMES)}), "
        f"tags(从这些里挑适用的，0-4个，数组：{'、'.join(TAG_VOCAB)})。"
        "只输出 JSON。\n"
        f"分类说明：{cat_guide}\n"
        f"项目名：{name}\n项目简介：{desc}\nREADME 摘录：\n---\n{(readme or '')[:5500]}\n---")


# ── 出口①：插件中心 ───────────────────────────────────────────────────
def save_skill(owner, repo, meta, skill_dir, dir_name, files, apply, publish, no_ai, license_warn):
    branch = meta["branch"]
    md = fetch_raw(owner, repo, branch, (skill_dir + "/SKILL.md") if skill_dir else "SKILL.md")
    risky = scan_risky(md)
    ai = None if no_ai else ai_skill(md)
    if not no_ai and not ai:
        raise RuntimeError(f"AI加工失败(skill {dir_name})，跳过不存空卡，下轮重试")
    pid = slugify_skill(f"{owner}-{repo}-{dir_name}")
    summary = (ai or {}).get("summary_zh") or (meta.get("description") or repo)
    if license_warn:
        summary = f"⚠️[{license_warn}] " + summary
    tags = list(dict.fromkeys((meta.get("topics") or [])[:6] + [f"license:{meta.get('license') or 'NONE'}"]))
    if license_warn:
        tags.append("待法务确认")
    if risky:
        tags.append("⚠️风险待审")
    packet = {
        "plugin_id": pid, "kind": "skill",
        "display_name_zh": (ai or {}).get("display_name_zh") or dir_name, "display_name_en": dir_name,
        "summary_zh": summary, "description_zh": (ai or {}).get("description_zh") or "",
        "usage_zh": (ai or {}).get("usage_zh") or "", "category": (ai or {}).get("category") or "其他",
        "tags": tags, "source": "github",
        "source_url": f"https://github.com/{owner}/{repo}" + (f"/tree/{branch}/{skill_dir}" if skill_dir else ""),
        "upstream_id": f"{owner}/{repo}", "compatible_tools": ["claudecode", "codex"],
        "stars": meta["stars"], "forks": meta["forks"],
        "install_payload": {"repo": f"{owner}/{repo}", "path": skill_dir, "files": files, "ref": branch},
        "status": "draft",
    }
    print(f"    [插件] {skill_dir or 'root'} → {pid}")
    print(f"           {packet['display_name_zh']} | {summary[:56]}" + (f" | 风险{risky}" if risky else ""))
    if not apply:
        print("           [dry-run] 未写库")
        return
    st, res = operator("/api/operator/desktop/plugins/save", packet, "POST")
    ok = st == 200 and isinstance(res, dict) and not res.get("error")
    print(f"           save HTTP {st} {'✅' if ok else '❌ ' + str(res)[:100]}")
    if ok and publish:
        st, _ = operator("/api/operator/desktop/plugins/publish", {"plugin_id": pid, "status": "published"}, "POST")
        print(f"           publish HTTP {st} {'✅ 上架' if st == 200 else '❌'}")


# ── 出口②：内容中心 GitHub 热门卡 ─────────────────────────────────────
def save_card(owner, repo, meta, cand, apply, visible, no_ai):
    branch = meta["branch"]
    readme = fetch_raw(owner, repo, branch, "README.md") or fetch_raw(owner, repo, branch, "README.en.md")
    ai = None if no_ai else ai_card(repo, meta.get("description") or "", readme)
    if not no_ai and not ai:
        raise RuntimeError(f"AI加工失败(card {owner}/{repo})，跳过不存空卡，下轮重试")
    title = (ai or {}).get("display_name_zh") or repo
    install_cmd = ((ai or {}).get("install_zh") or "").strip() or f"git clone https://github.com/{owner}/{repo}.git"
    cat = (ai or {}).get("category")
    cat = cat if cat in CAT_NAMES else "其他"
    raw_tags = (ai or {}).get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in re.split(r"[、,，\s]+", raw_tags) if t.strip()]
    tags = [t for t in raw_tags if t in TAG_VOCAB][:4]
    print(f"             分类: {cat} | 标签: {tags}")
    item = {
        "repoFullName": f"{owner}/{repo}", "sourceId": f"{owner}/{repo}",
        "title": title, "summary": (ai or {}).get("summary_zh") or (meta.get("description") or ""),
        "url": f"https://github.com/{owner}/{repo}", "language": meta.get("language"),
        "range": "weekly", "stars": meta["stars"], "forks": meta["forks"],
        "contributors": fetch_contributors(owner, repo),
        "useCasesZh": (ai or {}).get("use_cases_zh") or "", "usageZh": (ai or {}).get("usage_zh") or "",
        "installZh": install_cmd, "isVisible": bool(visible),
        "metadata": {"category": cat, "tags": tags},
    }
    print(f"    [内容卡] {owner}/{repo} → {title} | {item['summary'][:56]}")
    if not apply:
        print("             [dry-run] 未写库")
        return
    st, res = operator("/api/operator/content/github-trending/upsert", {"items": [item], "replace": False}, "POST")
    ok = st == 200 and isinstance(res, dict) and not res.get("error")
    print(f"             upsert HTTP {st} {'✅ ' + ('已展示' if visible else '已入库(未展示,待审)') if ok else '❌ ' + str(res)[:100]}")


# ── 近期涨星：刷新现有内容卡的 stars/starsAdded(不重跑 AI) ──────────────
def refresh_cards():
    st, lst = operator("/api/operator/content/github-trending?range=weekly")
    items = lst.get("items", []) if isinstance(lst, dict) else []
    state = load_state()
    snaps = state.setdefault("stars_snapshot", {})
    n = 0
    for it in items:
        m = it.get("metadata") or {}
        repo = m.get("repoFullName") or it.get("source_id") or ""
        if "/" not in repo:
            continue
        owner, name = repo.split("/", 1)
        try:
            d = gh(f"https://api.github.com/repos/{owner}/{name}")
        except Exception as e:
            print(f"  [refresh] {repo} 取数失败: {e}", file=sys.stderr)
            continue
        cur = d.get("stargazers_count", 0)
        prev = snaps.get(repo, {}).get("stars")
        added = max(cur - prev, 0) if isinstance(prev, int) else 0
        snaps[repo] = {"stars": cur, "at": NOW.isoformat()}
        item = {
            "repoFullName": repo, "sourceId": repo, "title": it.get("title"),
            "summary": it.get("summary"), "url": it.get("url"),
            "language": m.get("language"), "range": "weekly",
            "stars": cur, "starsAdded": added, "forks": d.get("forks_count", m.get("forks", 0)),
            "contributors": m.get("contributors", 0),
            "useCasesZh": m.get("useCasesZh", ""), "usageZh": m.get("usageZh", ""),
            "installZh": m.get("installZh", ""), "isVisible": True,
            "metadata": {"category": m.get("category", ""), "tags": m.get("tags", [])},
        }
        operator("/api/operator/content/github-trending/upsert", {"items": [item], "replace": False}, "POST")
        print(f"  [refresh] {repo}: ⭐{cur} (近期 +{added})")
        n += 1
        time.sleep(0.3)
    save_state(state)
    print(f"刷新 {n} 张内容卡的近期涨星(快照已记入 .github_state.json)")


# ── 路由：一仓自动分流 ────────────────────────────────────────────────
def route_repo(owner_repo, cand=None, apply=False, publish=False, visible=False, no_ai=False, allow_any=False, max_skills=SKILL_MAX, include_dot=False):
    owner, repo = owner_repo.split("/", 1)
    # 跨源去重防线:该仓若已在客户端任意中心(插件/内容卡),直接跳过,绝不重复入库
    if LIVE_IDX is not None and dedup.repo_exists(LIVE_IDX, owner_repo):
        print(f"\n=== {owner_repo}  ⏭️ 已在客户端(跨源去重),跳过 ===")
        return "exists"
    meta = fetch_meta(owner, repo)
    spdx = meta["license"]
    print(f"\n=== {owner_repo}  ⭐{meta['stars']}  license={spdx}  lang={meta.get('language')} ===")
    try:
        skills = find_skill_md(owner, repo, meta["branch"], include_dot=include_dot)
    except Exception as e:
        skills = []
        print(f"  (文件树读取失败: {e})")

    # 分流(产品边界)：
    #   skill 类(有 SKILL.md)        → 出口①插件中心(license 合规才收；不合规则跳过，不混进项目热门)
    #   普通项目(无 SKILL.md)         → 出口②内容中心 GitHub 热门(大白话引流卡)
    if skills:
        if len(skills) > max_skills:
            print(f"  ⓘ 含 {len(skills)} 个 skill(超过上限 {max_skills}，技能合集仓) → 改做引流内容卡")
            save_card(owner, repo, meta, cand or {}, apply, visible, no_ai)
            return "card-collection"
        if len(skills) > SKILL_MAX:
            print(f"  ⚠️ 含 {len(skills)} 个 skill(>{SKILL_MAX})，--max-skills={max_skills} 放行，逐个入插件")
        # license 门已放宽：非白名单不再跳过，改为收录 + 打警告标签(审核时可剔除)
        warn = "" if license_ok(spdx) else (spdx or "NO-LICENSE")
        if warn:
            print(f"  ⚠️ license「{spdx}」非白名单 → 仍收录并标警告(审核时可剔)")
        print(f"  → 出口①插件中心({len(skills)} 个 skill)")
        for sd, dn, files in skills:
            save_skill(owner, repo, meta, sd, dn, files, apply, publish, no_ai, warn)
        return "plugin"
    else:
        print("  → 出口②内容中心(普通项目，做大白话引流卡)")
        save_card(owner, repo, meta, cand or {}, apply, visible, no_ai)
        return "card"


def main():
    ap = argparse.ArgumentParser(description="GitHub 发现+加工+双线入库 (P0.5, 路线Y)")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--add", metavar="owner/repo")
    ap.add_argument("--apply", action="store_true", help="真写库(默认 dry-run)")
    ap.add_argument("--publish", action="store_true", help="插件 save 后直接上架")
    ap.add_argument("--visible", action="store_true", help="内容卡直接展示(默认入库不展示)")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--allow-any-license", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="只刷新现有内容卡的近期涨星(不发现/不加工)")
    ap.add_argument("--add-list", dest="add_list", metavar="FILE", help="批量处理一个 owner/repo 清单文件(每行一个)")
    ap.add_argument("--include-dot-skills", dest="include_dot", action="store_true", help="收 .claude/.codex 等 dot 目录下的 SKILL.md(用于正经技能包)")
    ap.add_argument("--max-skills", dest="max_skills", type=int, default=SKILL_MAX, help=f"单仓技能数超过此值才当合集做内容卡(默认{SKILL_MAX}；调高可把策展合集的技能逐个收进插件中心)")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if not (GITHUB_TOKEN and OP_TOKEN):
        print("❌ 缺少 GITHUB_TOKEN 或 OPERATOR_TOKEN(检查 .env)", file=sys.stderr)
        sys.exit(1)
    print(f"[github-import] 模式={'APPLY(写库)' if args.apply else 'DRY-RUN'} "
          f"publish={args.publish} visible={args.visible} ai={'off' if args.no_ai else RELAY_MODEL}")

    if args.refresh:
        refresh_cards()
        return

    if args.add_list:
        lines = Path(args.add_list).read_text(encoding="utf-8").splitlines()
        repos = []
        for ln in lines:
            ln = ln.strip()
            if ln and not ln.startswith("#") and "/" in ln:
                repos.append(ln.split()[0])
        repos = list(dict.fromkeys(repos))
        print(f"[add-list] {len(repos)} 个仓待处理")
        build_live_index()
        state = load_state()
        processed = state.setdefault("processed", {})
        new = 0
        for r in repos:
            if r in processed:
                print(f"  ⏭️ {r} 已处理过，跳过")
                continue
            try:
                outcome = route_repo(r, apply=args.apply, publish=args.publish,
                                     visible=args.visible, no_ai=args.no_ai, allow_any=args.allow_any_license, max_skills=args.max_skills, include_dot=args.include_dot)
                if args.apply:
                    processed[r] = {"at": NOW.isoformat(), "outcome": outcome}
                    save_state(state)
                    new += 1
            except Exception as e:
                print(f"  [出错跳过] {r}: {e}", file=sys.stderr)
            time.sleep(0.5)
        if args.apply:
            print(f"\n新处理 {new} 个")
            print("=== 刷新涨星 ===")
            refresh_cards()
        return

    if args.add:
        build_live_index()
        route_repo(args.add, apply=args.apply, publish=args.publish,
                   visible=args.visible, no_ai=args.no_ai, allow_any=args.allow_any_license, max_skills=args.max_skills, include_dot=args.include_dot)
        return

    if args.discover:
        cands = discover(args.limit)
        print(f"\n=== 冷门发现：{len(cands)} 个候选(按增速排序，标注预判去向) ===")
        for c in cands:
            dest = "→插件" if (c["is_skill_topic"] and license_ok(c["license"])) else "→内容卡"
            lic = c["license"] or "无"
            print(f"  {dest:5} {c['repo']}  ⭐{c['stars']}(+{c['vel']}/天) fs={c['fs']} [{lic}] | {c['desc'][:50]}")
        if args.apply:
            print("\n=== 逐个加工并入库(自动分流，已处理过的跳过) ===")
            build_live_index()
            state = load_state()
            processed = state.setdefault("processed", {})
            new_cnt = 0
            for c in cands:
                if c["repo"] in processed:
                    print(f"  ⏭️ {c['repo']} 已处理过({processed[c['repo']].get('at','')[:10]})，跳过")
                    continue
                try:
                    outcome = route_repo(c["repo"], cand=c, apply=True, publish=args.publish,
                                         visible=args.visible, no_ai=args.no_ai)
                    processed[c["repo"]] = {"at": NOW.isoformat(), "outcome": outcome}
                    save_state(state)
                    new_cnt += 1
                except Exception as e:
                    print(f"  [出错跳过] {c['repo']}: {e}", file=sys.stderr)
                time.sleep(1)
            print(f"\n本次新处理 {new_cnt} 个（已记入 .github_state.json，下次不再重复）")
            print("\n=== 刷新所有内容卡的近期涨星 ===")
            refresh_cards()
        else:
            print("\n(加 --apply 才会加工并入库)")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
