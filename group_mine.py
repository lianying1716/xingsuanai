#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按用户群体挖掘 GitHub 项目+skill → 自动发布 → 客户端校验 → 回复清单
============================================================
读 user_groups.json(12 类群体+关键词) → 每群 GitHub 搜索候选 →
复用 github_import.py 自动发布(--publish 插件直接上架 / --visible 内容卡直接展示) →
回读「客户端接口」确认真上架可见(而非停草稿/后台) → 产出分群体回复清单(打印+写 logs)。

用法:
  python3 group_mine.py            # 全流程:搜→发布→校验→清单
  python3 group_mine.py --dry      # 只搜并打印候选,不发布(验证用)
配套定时:NAS cron 每 3 天。凭证读同目录 .env。
"""
import json, subprocess, sys, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
GH = ENV.get("GITHUB_TOKEN", "")
OPB = ENV.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/")
DRY = "--dry" in sys.argv


def gh_search(q, n):
    full = f"{q} stars:>200 pushed:>2026-04-01"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": full, "sort": "stars", "order": "desc", "per_page": max(n, 5)})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {GH}", "User-Agent": "x", "Accept": "application/vnd.github+json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30)).get("items", [])
    except Exception as e:
        print(f"[search] {q} 失败: {e}", file=sys.stderr); return []


def client_get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(OPB + path, headers={"User-Agent": "x"}), timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def main():
    cfg = json.loads((HERE / "user_groups.json").read_text(encoding="utf-8"))
    repo_group = {}                       # owner/repo -> 首次命中的群体
    for g in cfg["groups"]:
        lim = g.get("limit", 4); got = 0
        for q in g["queries"]:
            for it in gh_search(q, lim):
                fn = it["full_name"]
                if fn not in repo_group:
                    repo_group[fn] = g["name"]; got += 1
                if got >= lim:
                    break
            time.sleep(5)
            if got >= lim:
                break
        print(f"[group] {g['name']}: +{got}")
    repos = list(repo_group.keys())
    (HERE / "candidates_groups.txt").write_text("# 按用户群体挖掘候选\n" + "\n".join(repos) + "\n", encoding="utf-8")
    print(f"\n[group] 共 {len(repos)} 个候选 → candidates_groups.txt")
    if DRY:
        for fn, grp in repo_group.items():
            print(f"  [{grp}] {fn}")
        return

    # 自动发布:--publish(skill 直接上架) + --visible(内容卡直接展示)
    subprocess.run([sys.executable, str(HERE / "github_import.py"), "--add-list", str(HERE / "candidates_groups.txt"),
                    "--apply", "--publish", "--visible", "--max-skills", "20", "--include-dot-skills"], cwd=str(HERE))

    # 客户端校验(走客户端真实接口,确认上架可见,而非停草稿/后台)
    pitems = (client_get("/api/desktop/v1/plugins").get("items")) or []
    citems = (client_get("/api/content/github-trending?page_size=200").get("items")) or []

    def in_plugins(fn):
        f = fn.lower()                       # owner/repo
        dash = f.replace("/", "-")           # 真实 plugin_id 用短横线
        for i in pitems:
            blob = (str(i.get("plugin_id", "")) + "|" + str(i.get("source_url", "")) +
                    "|" + str(i.get("upstream_id", ""))).lower()
            if f in blob or dash in blob:
                return True
        return False

    def in_cards(fn):
        f = fn.lower()
        return any(f in (str(i.get("url", "")) + "|" + str(i.get("source_id", ""))).lower() for i in citems)

    byg = defaultdict(list)
    for fn, grp in repo_group.items():
        byg[grp].append(fn)
    lines = [f"# 按用户群体挖掘 · 发布清单 {datetime.now(timezone.utc).isoformat()[:16]}",
             f"# 候选 {len(repos)} 个 | 插件中心 {len(pitems)} | 内容卡 {len(citems)}"]
    ok = miss = 0
    for grp, fns in byg.items():
        lines.append(f"\n【{grp}】")
        for fn in fns:
            if in_plugins(fn):
                loc = "✅ 插件中心"; ok += 1
            elif in_cards(fn):
                loc = "✅ 内容中心"; ok += 1
            else:
                loc = "⚠️ 未在客户端(查日志/可能加工失败)"; miss += 1
            lines.append(f"  {loc}  {fn}")
    lines.append(f"\n合计:客户端可见 {ok} | 未上架 {miss}")
    report = "\n".join(lines)
    try:
        (HERE.parent / "logs" / f"group_mine_{datetime.now().strftime('%Y%m%d')}.log").write_text(report, encoding="utf-8")
    except Exception:
        pass
    print("\n" + report)


if __name__ == "__main__":
    main()
