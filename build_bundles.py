#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
痛点技能包构建器 —— 把现成插件按用户痛点「组合搭配」成一键全装的合集(策展,不写技能)
============================================================
流程:
  1. 拉客户端真实插件目录(plugin_id/中文名/分类/标签)
  2. 按 user_groups.json 的人群,让 gpt-5.4-mini 提议痛点包:
     {pain 痛点一句话, title 卡片标题, summary 大白话, icon, plugin_ids(3-4个真实id)}
  3. 校验 plugin_ids 真实存在 → 写 bundles.json(草稿,给人工确认)
  4. --apply 推送后端(默认草稿不可见);--publish 同时上架可见(人工确认达标后才用)

用法:
  python3 build_bundles.py                 # 生成提议 → 写 bundles.json,打印给人工看(不推送)
  python3 build_bundles.py --from-file     # 直接用人工改好的 bundles.json(跳过AI)
  python3 build_bundles.py --from-file --apply --publish   # 把确认好的包推上线
凭证读同目录 .env。
"""
import argparse, json, re, sys, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
RELAY = ENV.get("NEXAI_RELAY_BASE_URL", "https://api.xsai5.xyz/v1").rstrip("/")
RKEY = ENV.get("NEXAI_RELAY_API_KEY", ""); MODEL = ENV.get("NEXAI_RELAY_MODEL", "gpt-5.4-mini")
OPB = ENV.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/"); OPT = ENV.get("OPERATOR_TOKEN", "")
BUNDLES_PATH = HERE / "bundles.json"


def client_get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(OPB + path, headers={"User-Agent": "x"}), timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def op_post(path, data):
    req = urllib.request.Request(OPB + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json", "x-operator-token": OPT}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"_raw": e.read().decode()[:200]}


def chat(prompt, retries=2):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 900}
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(f"{RELAY}/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {RKEY}", "Content-Type": "application/json"})
            raw = json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"].strip()
            return json.loads(re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip())
        except Exception as e:
            if a < retries:
                time.sleep(3)
            else:
                print(f"[ai] 失败: {e}", file=sys.stderr)
    return None


def fetch_catalog():
    pl = client_get("/api/desktop/v1/plugins").get("items", []) or []
    cat = {}
    for p in pl:
        pid = p.get("plugin_id")
        if not pid:
            continue
        cat[pid] = {
            "name": p.get("display_name_zh") or p.get("display_name_en") or pid,
            "category": p.get("category") or "",
            "tags": (p.get("tags") or [])[:4],
            "summary": (p.get("summary_zh") or "")[:60],
        }
    return cat


def propose(cat, groups):
    # 紧凑目录文本喂 AI(只给 id|名|分类),控制体积
    lines = [f"{pid}|{v['name']}|{v['category']}" for pid, v in cat.items()]
    catalog_txt = "\n".join(lines)
    bundles = []
    seen_ids = set()
    for g in groups:
        prompt = (
            "你是星算助手(AI工具桌面端)的运营。下面是插件中心现有插件目录(格式 plugin_id|中文名|分类)。\n"
            f"请为「{g['name']}」这类用户({g.get('desc','')}),从目录里**挑选现成插件组合**成 1-2 个"
            "「痛点技能包」——一句痛点 + 3~4 个能一起解决它的插件。要求:\n"
            "- 必须直击该人群真实痛点,用最通俗的话(给从没用过AI的小白看)\n"
            "- plugin_ids 只能从目录里原样复制,绝不能编造\n"
            "- 输出严格 JSON 数组,每个元素: {bundle_id(英文短横线,如 ecom-traffic), pain(8字内痛点,如\"新店没流量\"), "
            "title(标题,如\"新店没流量？这样搭\"), summary(一句大白话说清这包帮你干嘛), icon(一个emoji), plugin_ids(3-4个数组)}\n"
            "只输出 JSON 数组。\n\n目录:\n" + catalog_txt)
        arr = chat(prompt)
        if not isinstance(arr, list):
            print(f"  [{g['name']}] AI 未产出有效提议,跳过", file=sys.stderr); continue
        kept = 0
        for b in arr:
            if not isinstance(b, dict):
                continue
            pids = [p for p in (b.get("plugin_ids") or []) if p in cat]  # 只留真实存在的
            if len(pids) < 2:
                continue
            bid = re.sub(r"[^a-z0-9\-]+", "-", str(b.get("bundle_id") or b.get("pain") or "").lower()).strip("-")[:64]
            if not bid or bid in seen_ids:
                continue
            seen_ids.add(bid)
            bundles.append({
                "bundle_id": bid, "group": g["name"], "pain": str(b.get("pain") or "")[:40],
                "title": str(b.get("title") or b.get("pain") or "")[:80],
                "summary": str(b.get("summary") or "")[:200], "icon": str(b.get("icon") or "🎯")[:8],
                "plugin_ids": pids[:4], "sort": len(bundles), "status": "draft", "is_visible": False,
            })
            kept += 1
        print(f"  [{g['name']}] +{kept} 个痛点包")
        time.sleep(1)
    return bundles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", action="store_true", help="用人工改好的 bundles.json,跳过 AI 提议")
    ap.add_argument("--apply", action="store_true", help="推送后端")
    ap.add_argument("--publish", action="store_true", help="同时上架可见(人工确认达标后才用)")
    args = ap.parse_args()

    if args.from_file:
        bundles = json.loads(BUNDLES_PATH.read_text(encoding="utf-8")).get("bundles", [])
        print(f"[bundles] 从文件读取 {len(bundles)} 个包")
    else:
        cat = fetch_catalog()
        print(f"[bundles] 插件目录 {len(cat)} 个")
        groups = json.loads((HERE / "user_groups.json").read_text(encoding="utf-8")).get("groups", [])
        bundles = propose(cat, groups)
        BUNDLES_PATH.write_text(json.dumps({"bundles": bundles}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[bundles] 共提议 {len(bundles)} 个 → 已写 bundles.json(草稿)")

    # 打印清单给人工确认
    cat = locals().get("cat") or fetch_catalog()
    print("\n===== 痛点技能包清单(人工确认用) =====")
    for b in bundles:
        names = "、".join(cat.get(p, {}).get("name", p) for p in b["plugin_ids"])
        print(f"\n{b.get('icon','')} 【{b['group']}】{b['title']}")
        print(f"   痛点:{b['pain']} | {b.get('summary','')}")
        print(f"   含技能({len(b['plugin_ids'])}):{names}")

    if args.apply:
        if args.publish:
            for b in bundles:
                b["status"] = "published"; b["is_visible"] = True
        st, res = op_post("/api/operator/desktop/plugin-bundles/upsert", {"bundles": bundles, "replace": False})
        print(f"\n[bundles] 推送 HTTP {st} {res} {'(已上架可见)' if args.publish else '(草稿,未展示)'}")
    else:
        print("\n[bundles] 仅生成,未推送。确认无误后:python3 build_bundles.py --from-file --apply --publish")


if __name__ == "__main__":
    main()
