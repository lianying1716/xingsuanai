#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cocoloop 发现源 → 回 GitHub 抓真仓(严格:搜不到=丢弃)
============================================================
cocoloop(hub.cocoloop.cn)是个 skill 榜单站,但它的安装是自家 zip、不可靠。
我们只把它当【发现源】:取热门技能的 名字+作者 → 回 GitHub 按名+作者搜真仓 →
命中真仓才交给 github_import 加工(真实 SKILL.md + 可靠安装命令 + 四段说明)。
搜不到对应 GitHub 真仓的 → 直接丢弃(保证每条都有可靠完整安装)。

用法:
  python3 cocoloop_mine.py                       # 解析候选,写 candidates_cocoloop.txt(不入库)
  python3 cocoloop_mine.py --apply --publish      # 解析后交 github_import 加工并上架
  --limit N(默认60) --sort downloads|recommend|stars
凭证读同目录 .env。
"""
import argparse, json, re, subprocess, sys, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
GH = ENV.get("GITHUB_TOKEN", "")
COCO_API = "https://api.cocoloop.cn/api/v1/store/skills"


def coco_top(sort, limit):
    out, page = [], 1
    while len(out) < limit:
        q = urllib.parse.urlencode({"sort": sort, "page": page, "page_size": 20})
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{COCO_API}?{q}",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://hub.cocoloop.cn/"}), timeout=20) as r:
                d = json.loads(r.read())["data"]
        except Exception as e:
            print(f"[coco] 拉取第{page}页失败: {e}", file=sys.stderr); break
        items = d.get("items", [])
        if not items:
            break
        out.extend(items)
        if page >= d.get("pages", 1):
            break
        page += 1
        time.sleep(0.4)
    return out[:limit]


def _gh(url):
    h = {"User-Agent": "x", "Accept": "application/vnd.github+json"}
    if GH:
        h["Authorization"] = f"Bearer {GH}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.loads(r.read())


def gh_has_skill(full):
    """该仓是否真有 SKILL.md(含子目录,排除 dot 目录)。没有=不是可安装的技能,丢弃。"""
    try:
        owner, repo = full.split("/", 1)
        br = _gh(f"https://api.github.com/repos/{owner}/{repo}").get("default_branch", "main")
        tree = _gh(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{br}?recursive=1").get("tree", [])
        for e in tree:
            p = e.get("path", "")
            if (p == "SKILL.md" or p.endswith("/SKILL.md")) and not any(s.startswith(".") for s in p.split("/")[:-1]):
                return True
    except Exception:
        pass
    return False


def gh_resolve(name, author):
    """按 名字+作者 解析成 owner/repo,且必须真含 SKILL.md 才返回(严格保证可安装)。
    作者对得上优先;否则名字真匹配+有星标做候选。都要过 SKILL.md 检查。搜不到返回 None。"""
    nlow = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    alow = re.sub(r"[^a-z0-9]", "", (author or "").lower())
    if len(nlow) < 3:
        return None
    q = urllib.parse.urlencode({"q": f"{name} in:name", "sort": "stars", "order": "desc", "per_page": 8})
    try:
        items = _gh(f"https://api.github.com/search/repositories?{q}").get("items", [])
    except Exception:
        return None
    primary, fallback = [], []   # 作者匹配优先,名字匹配兜底
    for it in items:
        owner, repo = it["full_name"].split("/", 1)
        rlow = re.sub(r"[^a-z0-9]", "", repo.lower())
        olow = re.sub(r"[^a-z0-9]", "", owner.lower())
        if "awesome" in repo.lower() and "awesome" not in (name or "").lower():
            continue
        if not (nlow in rlow or rlow in nlow):
            continue
        if alow and (alow == olow or alow in olow or olow in alow):
            primary.append(it["full_name"])
        elif it["stargazers_count"] >= 50:
            fallback.append(it["full_name"])
    for full in primary + fallback:           # 按优先级逐个验 SKILL.md,第一个有的就用
        if gh_has_skill(full):
            return full
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--sort", default="downloads", choices=["downloads", "recommend", "stars", "rating"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    if not GH:
        print("❌ 缺 GITHUB_TOKEN", file=sys.stderr); sys.exit(1)

    skills = coco_top(args.sort, args.limit)
    print(f"[coco] 拉到 {len(skills)} 个热门技能(sort={args.sort})")
    resolved, dropped, seen = {}, [], set()
    for s in skills:
        name, author = s.get("name", ""), s.get("author", "")
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in seen:
            continue
        seen.add(key)
        full = gh_resolve(name, author)
        if full:
            resolved[full] = (name, author)
            print(f"  ✅ {name} (by {author}) → {full}")
        else:
            dropped.append(f"{name} (by {author})")
            print(f"  ⏭️ {name} (by {author}) → 无 GitHub 真仓,丢弃")
        time.sleep(1)

    cand = HERE / "candidates_cocoloop.txt"
    cand.write_text("# cocoloop 热门 → GitHub 真仓(严格:无仓已丢弃)\n" + "\n".join(resolved) + "\n", encoding="utf-8")
    print(f"\n[coco] 命中 GitHub 真仓 {len(resolved)} 个 / 丢弃 {len(dropped)} 个 → {cand.name}")

    if args.apply and resolved:
        cmd = [sys.executable, str(HERE / "github_import.py"), "--add-list", str(cand),
               "--apply", "--include-dot-skills", "--max-skills", "20"]
        if args.publish:
            cmd.append("--publish")
        print(f"[coco] 交 github_import 加工: {' '.join(cmd[1:])}")
        subprocess.run(cmd, cwd=str(HERE))
    elif not args.apply:
        print("[coco] 仅解析,未入库。确认后: python3 cocoloop_mine.py --apply --publish")


if __name__ == "__main__":
    main()
