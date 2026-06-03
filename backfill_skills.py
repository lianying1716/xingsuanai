#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填:给"四段字段存在之前导入的"老技能补 核心用法/触发场景/优劣特点/适用人群 + 客观说明
============================================================
不重导、不触发去重、不改状态。对每个缺四段的 github 技能:
  用它已存的 install_payload(repo/path/ref) 回 GitHub 拉 SKILL.md/README →
  跑 github_import 里的同一套专业提示词 ai_skill → 整体回存(覆盖说明类字段)。
幂等:已有 core_usage 的跳过;AI 失败的跳过(下次再跑补)。

用法:
  python3 backfill_skills.py --limit 5      # 先小批验证
  python3 backfill_skills.py                # 全量回填(github 缺四段的)
凭证读同目录 .env。
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path
import github_import as gi   # 复用 ai_skill / fetch_raw / fetch_version / operator

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
OPB = ENV.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/")
TOK = ENV.get("OPERATOR_TOKEN", "")


def op_list():
    req = urllib.request.Request(OPB + "/api/operator/desktop/plugins", headers={"x-operator-token": TOK, "User-Agent": "x"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read()).get("items", [])


def op_save(packet):
    req = urllib.request.Request(OPB + "/api/operator/desktop/plugins/save", data=json.dumps(packet).encode(),
                                 headers={"x-operator-token": TOK, "Content-Type": "application/json", "User-Agent": "x"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=40).read())
    except Exception as e:
        return {"error": str(e)}


def _s(v):
    if isinstance(v, list):
        return "\n".join(f"• {str(x).strip()}" for x in v if str(x).strip())
    return str(v or "").strip()


def ai_clawhub(p):
    """clawhub 技能无 SKILL.md,只能基于已有简介客观扩写四段(明确不编造)。"""
    name = p.get("display_name_en") or p.get("display_name_zh") or ""
    summ = p.get("summary_zh") or ""
    cat = p.get("category") or ""
    tags = "、".join(p.get("tags") or [])
    return gi._chat(
        "你是严谨的技术工具编辑。下面是 ClawHub(OpenClaw 技能市场)上一个技能的【已有列表信息】"
        "(没有源码/SKILL.md,只有简介)。请只依据这些信息客观推断,**不要编造不存在的具体功能或参数**,"
        "拿不准的就笼统说。陈述句、客观、禁营销腔。输出严格 JSON："
        "core_usage(核心用法,2-3句), triggers(触发场景,用『、』分隔), "
        "pros_cons(优劣特点:亮点 + 注明'信息来自ClawHub列表、未核验源码'这一前提), "
        "audience(适用人群)。只输出 JSON。\n"
        f"技能名:{name}\n简介:{summ}\n分类:{cat}\n标签:{tags}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个(0=全部)")
    ap.add_argument("--clawhub", action="store_true", help="回填 clawhub 技能(基于已有简介扩写,非源码)")
    args = ap.parse_args()

    if args.clawhub:
        items = op_list()
        todo = [p for p in items if p.get("source") == "clawhub" and not (p.get("usage_detail") or {}).get("core_usage")]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[backfill-clawhub] 待回填 {len(todo)} 个")
        ok = fail = 0
        for i, p in enumerate(todo, 1):
            ai = ai_clawhub(p)
            if not ai or not ai.get("core_usage"):
                print(f"  [{i}/{len(todo)}] {p['plugin_id']} AI失败,跳过"); fail += 1; continue
            ip = p.get("install_payload") or {}
            upd = dict(p)
            upd["usage_detail"] = {
                "core_usage": _s(ai.get("core_usage")), "triggers": _s(ai.get("triggers")),
                "pros_cons": _s(ai.get("pros_cons")), "audience": _s(ai.get("audience")),
                "install_official": (ip.get("command") or "").strip(),
            }
            r = op_save(upd)
            if r.get("error"):
                print(f"  [{i}/{len(todo)}] {p['plugin_id']} 回存失败"); fail += 1
            else:
                print(f"  [{i}/{len(todo)}] ✅ {p.get('display_name_zh')}"); ok += 1
            time.sleep(0.3)
        print(f"\n[backfill-clawhub] 完成:{ok} | 失败 {fail}")
        return

    def needs_fix(p):
        ud = p.get("usage_detail") or {}
        if not ud.get("core_usage"):
            return True
        # 已回填但某字段被 AI 存成了数组 → 也要修(归一化成字符串)
        return any(isinstance(ud.get(k), list) for k in ("core_usage", "triggers", "pros_cons", "audience"))

    items = op_list()
    todo = [p for p in items if p.get("source") == "github"
            and needs_fix(p)
            and (p.get("install_payload") or {}).get("repo")]
    if args.limit:
        todo = todo[:args.limit]
    print(f"[backfill] 待回填 github 技能 {len(todo)} 个")

    ok = fail = 0
    for i, p in enumerate(todo, 1):
        ip = p.get("install_payload") or {}
        repo_full = ip.get("repo", "")
        if "/" not in repo_full:
            continue
        owner, repo = repo_full.split("/", 1)
        path = (ip.get("path") or "").strip("/")
        ref = ip.get("ref") or "main"
        md = gi.fetch_raw(owner, repo, ref, (path + "/SKILL.md") if path else "SKILL.md")
        readme = gi.fetch_raw(owner, repo, ref, (path + "/README.md") if path else "README.md")
        if not md:
            print(f"  [{i}/{len(todo)}] {p['plugin_id']} 取不到 SKILL.md,跳过"); fail += 1; continue
        ai = gi.ai_skill(md, readme)
        if not ai or not ai.get("core_usage"):
            print(f"  [{i}/{len(todo)}] {p['plugin_id']} AI 加工失败,跳过(下次再补)"); fail += 1; continue
        cat = ai.get("category") if ai.get("category") in gi.SKILL_CATS else (p.get("category") or "其他")
        # 英文主标题:dir 是通用名时回退仓名
        dirn = path.split("/")[-1] if path else repo
        en = dirn if dirn and dirn.lower() not in ("skill", "skills", "src", "main", ".", repo.lower()) else repo
        upd = dict(p)  # 整体回存,只覆盖说明类字段,其余原样
        upd["display_name_en"] = en
        upd["display_name_zh"] = ai.get("display_name_zh") or p.get("display_name_zh") or en
        upd["summary_zh"] = ai.get("summary") or p.get("summary_zh") or ""
        upd["category"] = cat
        upd["version"] = p.get("version") or gi.fetch_version(owner, repo)
        upd["usage_detail"] = {
            "core_usage": _s(ai.get("core_usage")),
            "triggers": _s(ai.get("triggers")),
            "pros_cons": _s(ai.get("pros_cons")),
            "audience": _s(ai.get("audience")),
            "install_official": (ai.get("install_official") or "").strip(),
        }
        if ai.get("tags"):
            upd["tags"] = [str(t) for t in ai["tags"]][:4] + [t for t in (p.get("tags") or []) if "license" in str(t).lower()]
        r = op_save(upd)
        if r.get("error"):
            print(f"  [{i}/{len(todo)}] {p['plugin_id']} 回存失败: {str(r)[:80]}"); fail += 1
        else:
            print(f"  [{i}/{len(todo)}] ✅ {en}/{upd['display_name_zh']}"); ok += 1
        time.sleep(0.3)

    print(f"\n[backfill] 完成:回填 {ok} | 跳过/失败 {fail}")


if __name__ == "__main__":
    main()
