#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClawHub Top 技能 → 插件中心(宣传式收录)
============================================================
ClawHub(OpenClaw 的 skill 市场)技能不是 git 仓，装法是 `clawhub install <slug>`。
按"宣传"定位收录：展示 + 标注安装命令，source=clawhub，不做 git 安装。
  1. 拉 clawhub.ai Top N(按下载)
  2. gpt-5.4-mini 大白话加工(中文名/一句话/分类/标签)
  3. 存插件中心草稿(plugin_id=clawhub-<slug>, install=clawhub install)
用法: python3 clawhub_import.py --limit 50
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
CFG = {}
if ENV.exists():
    for ln in ENV.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); CFG[k.strip()] = v.strip()
RELAY = CFG.get("NEXAI_RELAY_BASE_URL", "https://api.xsai5.xyz/v1").rstrip("/")
RKEY = CFG.get("NEXAI_RELAY_API_KEY", ""); MODEL = CFG.get("NEXAI_RELAY_MODEL", "gpt-5.4-mini")
OPB = CFG.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/"); OPT = CFG.get("OPERATOR_TOKEN", "")
CAT_PATH = Path(__file__).resolve().parent / "github_categories.json"
CATS = json.loads(CAT_PATH.read_text(encoding="utf-8")) if CAT_PATH.exists() else {"categories": [], "tags": []}
CAT_NAMES = [c["name"] for c in CATS.get("categories", [])] or ["其他"]
TAGS = CATS.get("tags", [])


def chat(prompt, retries=2):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 500}
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(f"{RELAY}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {RKEY}", "Content-Type": "application/json"})
            raw = json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip()
            return json.loads(re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip())
        except Exception:
            if a < retries: time.sleep(3)
    return None


def op(path, data=None, method=None):
    req = urllib.request.Request(OPB + path, data=(json.dumps(data).encode() if data else None), method=method)
    req.add_header("x-operator-token", OPT)
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, {}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--apply", action="store_true"); args = ap.parse_args()
    url = "https://clawhub.ai/api/v1/skills?" + urllib.parse.urlencode({"sort": "downloads", "limit": args.limit})
    items = json.load(urllib.request.urlopen(url, timeout=30)).get("items", [])[:args.limit]
    print(f"[clawhub] 拉到 {len(items)} 个 Top 技能")
    done = 0
    for it in items:
        slug = it.get("slug");
        if not slug: continue
        name = it.get("displayName") or slug; summ = (it.get("summary") or "")[:400]
        dl = (it.get("stats") or {}).get("downloads") or (it.get("stats") or {}).get("installs") or 0
        ai = chat("把这个 OpenClaw 技能用大白话中文介绍给小白，输出严格 JSON："
                  "display_name_zh(8字内), summary_zh(一句话干嘛的), "
                  f"category(从这些选一个:{'/'.join(CAT_NAMES)}), tags(0-4个数组,从:{'、'.join(TAGS)})。只输出JSON。\n"
                  f"技能名:{name}\n说明:{summ}") or {}
        cat = ai.get("category") if ai.get("category") in CAT_NAMES else "其他"
        tags = [t for t in (ai.get("tags") or []) if t in TAGS][:4]
        pid = ("clawhub-" + re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-"))[:64]
        packet = {
            "plugin_id": pid, "kind": "skill",
            "display_name_zh": ai.get("display_name_zh") or name, "display_name_en": name,
            "summary_zh": ai.get("summary_zh") or summ,
            "usage_zh": f"在 OpenClaw 里运行：clawhub install {slug}",
            "category": cat, "tags": tags + ["ClawHub", f"下载{dl}"],
            "source": "clawhub", "source_url": f"https://clawhub.ai/skills/{slug}",
            "upstream_id": f"clawhub:{slug}", "compatible_tools": ["openclaw"],
            "stars": 0, "install_payload": {"method": "clawhub", "command": f"clawhub install {slug}", "slug": slug},
            "status": "draft",
        }
        print(f"  {slug} → {packet['display_name_zh']} [{cat}] dl={dl}")
        if args.apply:
            st, _ = op("/api/operator/desktop/plugins/save", packet, "POST")
            if st == 200: done += 1
            else: print("    ❌ save", st)
        time.sleep(0.3)
    print(f"\n[clawhub] {'写入草稿 '+str(done) if args.apply else 'DRY-RUN'} / {len(items)}")


if __name__ == "__main__":
    main()
