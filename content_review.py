#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
content_review.py —— 独立·干净上下文的内容审核器（审查关卡的物理隔离实现）
============================================================
为什么是独立脚本：它是独立进程，天然没有"创作时的对话记忆/意图"，是"上下文干净"的物理保证，
而不是靠自律。任何 AI 工具都能调它。

流程：
  1. 输入除污：剥掉注释/自检表/blockquote 等创作痕迹，只留会真正发出去的成品正文。
  2. 确定性合规扫描：按 platform_rules.json，全平台铁律 + 目标平台红线词（纯机器，不能被说服）。
     - 政策高压线(翻墙/VPN/外卡…)命中 = 硬性打回。
     - 绝对化词/官方/平台导流词 = 软标记，交 LLM 结合上下文判定。
  3. 全新隔离 LLM 调用：prompt 只含【成品正文 + 该平台规则 + 挑刺指令】，
     不含母题/弹药/创作意图；盲评(不告知阈值)；挑刺立场；每个扣分必须引原文证据。
  4. 合并裁定：政策硬命中 或 质量分<阈值 → 打回，给打回路由 + 改进项。

用法：
  python3 content_review.py --file draft.md --platform 知乎 --extract 知乎
  python3 content_review.py --file piece.md --platform 小红书
  echo "正文..." | python3 content_review.py --platform 小红书 --stdin
  附加：--json(机器输出) --runs N(独立跑N次取最狠,默认1)
"""
import argparse, json, re, sys, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
RELAY = ENV.get("NEXAI_RELAY_BASE_URL", "https://api.xsai5.xyz/v1").rstrip("/")
RKEY = ENV.get("NEXAI_RELAY_API_KEY", ""); MODEL = ENV.get("NEXAI_RELAY_MODEL", "gpt-5.4-mini")
RULES = json.loads((HERE / "platform_rules.json").read_text(encoding="utf-8"))


def scrub(text):
    """输入除污：去掉创作痕迹，只留可发布正文。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            out.append(ln); continue
        # 注释/自检/思路标记
        if s.startswith("〔") or s.startswith(">") or s.startswith("|") and ("命中" in s or "要素" in s):
            continue
        if s.startswith("# ") and ("闭环包" in s or "母题" in s):
            continue
        if re.match(r"^〔.*〕$", s):
            continue
        if s.startswith("> 〔") or "〕" in s and s.startswith("〔"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def extract_section(text, key):
    """从多平台合稿里抽某平台那段（按 '## ① 知乎' / '## ② 小红书' 之类标题）。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and key in ln:
            start = i; break
    if start is None:
        return text
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j; break
    return "\n".join(lines[start:end])


def deterministic_scan(content, platform):
    g = RULES["global_redlines"]
    hits = {"hard": [], "soft": []}

    def find(words, bucket, label):
        for w in words:
            if w and w in content:
                idx = content.find(w)
                ctx = content[max(0, idx - 12):idx + len(w) + 12].replace("\n", " ")
                hits[bucket].append({"rule": label, "word": w, "ctx": f"…{ctx}…"})

    find(g.get("政策高压线_绝不可提", []), "hard", "政策高压线(翻墙/VPN/外卡等)")
    find(g.get("其它禁词", []), "soft", "其它禁词(官方/破解等,需判上下文)")
    find(g.get("绝对化极限词_广告法第九条", []), "soft", "绝对化词(需判是否排名义)")
    p = RULES["platforms"].get(platform, {})
    find(p.get("risk_keywords", []), "soft", f"{platform}风险词(需判是否真违规)")
    return hits


def llm_review(content, platform, runs=1):
    p = RULES["platforms"].get(platform, {})
    rubric = RULES["quality_rubric"]
    baokuan = RULES["二审_爆款要素"]
    # 隔离 prompt：只给成品正文 + 该平台规则 + 挑刺指令，无任何创作上下文
    prompt = (
        "你是极其严格、默认这篇内容会扑街的【平台审核官 + 资深操盘手】。"
        "你不知道也不关心是谁写的、为什么写——只看这篇成品本身。你的 KPI 是【挑出问题】，不是夸。"
        "宁可偏向打回，也不放过毛病。\n"
        f"【发布目标平台】{platform}\n"
        f"【该平台规则(必须据此审)】{json.dumps(p, ensure_ascii=False)}\n"
        f"【质量评分维度】{json.dumps(rubric['dimensions'], ensure_ascii=False)}\n"
        f"【爆款二审要素】{json.dumps(baokuan, ensure_ascii=False)}\n"
        "【硬要求】：每一处扣分/合规问题，都必须【引用正文原文片段】作证据，引不出原文证据的判断不许给出。"
        "不要被内容本身说服。\n"
        "输出严格 JSON（只输出 JSON）：{"
        "\"compliance\":{\"pass\":bool,\"问题\":[{\"红线\":\"\",\"原文证据\":\"\"}]},"
        "\"quality\":{\"总分\":0-100,\"维度\":[{\"名\":\"\",\"得分\":0,\"满分\":0,\"扣分理由\":\"\",\"原文证据\":\"\"}]},"
        "\"爆款二审\":{\"人群\":{\"过\":bool,\"依据\":\"\"},\"情绪\":{\"过\":bool,\"依据\":\"\"},\"利益点\":{\"过\":bool,\"依据\":\"\"}},"
        "\"打回路由\":[\"配图/选题/装配/派生改写 等\"],\"改进项\":[\"\"]}\n"
        f"【待审成品正文】\n---\n{content[:6000]}\n---")
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 1200}
    results = []
    for _ in range(max(1, runs)):
        for attempt in range(3):
            try:
                req = urllib.request.Request(f"{RELAY}/chat/completions", data=json.dumps(body).encode(),
                                             headers={"Authorization": f"Bearer {RKEY}", "Content-Type": "application/json"})
                raw = json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"].strip()
                results.append(json.loads(re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()))
                break
            except Exception:
                if attempt == 2:
                    pass
    if not results:
        return None
    # 多次取最狠（最低分）
    return min(results, key=lambda r: (r.get("quality", {}) or {}).get("总分", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file"); ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--platform", required=True)
    ap.add_argument("--extract", help="多平台合稿里抽某段(如 知乎/小红书)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.platform not in RULES["platforms"]:
        print(f"❌ 未知平台 {args.platform}，可选：{list(RULES['platforms'])}", file=sys.stderr); sys.exit(1)

    raw = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
    if args.extract:
        raw = extract_section(raw, args.extract)
    content = scrub(raw)

    det = deterministic_scan(content, args.platform)
    llm = llm_review(content, args.platform, args.runs)
    threshold = RULES["quality_rubric"]["threshold"]

    score = (llm or {}).get("quality", {}).get("总分", 0)
    hard = bool(det["hard"])
    comp_pass = (not hard) and ((llm or {}).get("compliance", {}).get("pass", True))
    verdict = "通过" if (comp_pass and not hard and score >= threshold) else "打回"

    if args.json:
        print(json.dumps({"platform": args.platform, "verdict": verdict, "score": score,
                          "deterministic": det, "llm": llm}, ensure_ascii=False, indent=2)); return

    print(f"\n===== 独立审核报告（平台：{args.platform}）=====")
    print(f"裁定：{'✅ 通过' if verdict == '通过' else '⛔ 打回'}  | 质量分 {score}/{threshold}")
    print("\n— 合规（确定性扫描）—")
    print("  政策高压线硬命中：" + (json.dumps(det['hard'], ensure_ascii=False) if det['hard'] else "无 ✅"))
    if det["soft"]:
        print("  软标记(交AI判上下文)：" + json.dumps(det['soft'], ensure_ascii=False))
    if llm:
        cp = llm.get("compliance", {})
        print(f"\n— 合规（AI 判定）— {'✅过' if cp.get('pass') else '⛔有问题'}")
        for x in cp.get("问题", []):
            print(f"  • {x.get('红线')} | 证据：{x.get('原文证据')}")
        print("\n— 质量打分（每项带证据）—")
        for d in llm.get("quality", {}).get("维度", []):
            print(f"  {d.get('名')}: {d.get('得分')}/{d.get('满分')} — {d.get('扣分理由')}")
        bk = llm.get("爆款二审", {})
        print("\n— 爆款二审(内核) —")
        for k in ["人群", "情绪", "利益点"]:
            v = bk.get(k, {})
            print(f"  {k}: {'✅' if v.get('过') else '⛔'} {v.get('依据', '')}")
        print(f"\n打回路由：{llm.get('打回路由')}")
        print(f"改进项：{llm.get('改进项')}")
    else:
        print("\n⚠️ LLM 审核未返回(网络/中转)，仅确定性扫描可用，请重试。")


if __name__ == "__main__":
    main()
