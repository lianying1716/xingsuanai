#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
去重巡检 —— 每周扫一遍客户端,输出「疑似重复清单」给人工定夺(只报告,绝不自动删)
============================================================
覆盖三类:
  ① 精确重复:同 plugin_id / 同内容卡 url 出现多次(理论应为 0,upsert 已防;出现=异常)
  ② 跨中心重复:同一个 GitHub 仓既在插件中心又在内容中心
  ③ 模糊重复:剥掉 -skill/-mcp 等包装后,主名相同的不同条目(可能是 fork/换名/跨源同物)
用法: python3 dedup_audit.py        # 打印 + 写 logs/dedup_audit_YYYYMMDD.log
"""
import json, urllib.request
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict
import dedup

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
OPB = ENV.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/")


def g(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(OPB + path, headers={"User-Agent": "x"}), timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def main():
    plugins = (g("/api/desktop/v1/plugins") or {}).get("items", []) or []
    cards = (g("/api/content/github-trending?page_size=200") or {}).get("items", []) or []
    out = [f"# 去重巡检报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
           f"# 插件 {len(plugins)} | 内容卡 {len(cards)}"]

    # ① 精确重复
    pid_dup = {k: v for k, v in Counter(str(p.get("plugin_id")) for p in plugins).items() if v > 1}
    url_dup = {k: v for k, v in Counter(str(c.get("url")) for c in cards).items() if v > 1}
    out.append("\n## ① 精确重复(应为0)")
    out.append(f"  plugin_id 重复: {pid_dup or '无 ✅'}")
    out.append(f"  内容卡 url 重复: {url_dup or '无 ✅'}")

    # ② 跨中心重复(同仓在插件+内容卡)
    plug_repos = {}
    for p in plugins:
        cr = dedup.canon_repo(p.get("upstream_id")) or dedup.canon_repo(p.get("source_url"))
        if cr:
            plug_repos[cr] = p.get("plugin_id")
    card_repos = {}
    for c in cards:
        cr = dedup.canon_repo(c.get("url")) or dedup.canon_repo((c.get("metadata") or {}).get("repoFullName"))
        if cr:
            card_repos[cr] = c.get("title")
    cross = sorted(set(plug_repos) & set(card_repos))
    out.append("\n## ② 跨中心重复(同仓既在插件又在内容卡)")
    out.append("  " + ("、".join(cross) if cross else "无 ✅"))

    # ③ 模糊重复(主名相同的不同条目)
    clusters = defaultdict(list)
    for p in plugins:
        cr = dedup.canon_repo(p.get("upstream_id"))
        key = dedup.core_name(cr or p.get("upstream_id") or p.get("plugin_id"))
        clusters[key].append(("插件", str(p.get("plugin_id")), p.get("source")))
    for c in cards:
        cr = dedup.canon_repo(c.get("url"))
        key = dedup.core_name(cr or c.get("url"))
        clusters[key].append(("内容卡", str(c.get("title")), c.get("url")))
    fuzzy = {k: v for k, v in clusters.items() if k and len(v) > 1}
    out.append(f"\n## ③ 模糊重复(主名相同,需人工判断是否同物) — {len(fuzzy)} 组")
    if not fuzzy:
        out.append("  无 ✅")
    for key, members in sorted(fuzzy.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  ▸ 主名「{key}」({len(members)}条):")
        for kind, label, src in members:
            out.append(f"      [{kind}] {label}  ({src})")

    suspected = bool(pid_dup or url_dup or cross or fuzzy)
    out.append(f"\n结论:{'⚠️ 有疑似重复,请人工核对上面 ②③ 并在后台合并' if suspected else '✅ 无任何重复'}")
    report = "\n".join(out)
    try:
        (HERE.parent / "logs" / f"dedup_audit_{datetime.now().strftime('%Y%m%d')}.log").write_text(report, encoding="utf-8")
    except Exception:
        pass
    print(report)


if __name__ == "__main__":
    main()
