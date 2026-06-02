#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音视频卡片 → GitHub 项目抽取器 (Direction 1)
============================================================
抖音转录里只"口播"项目名、没有 URL。本脚本：
  1. 扫视频卡片(默认提到 github 的)，取前 N 张
  2. 分批喂 gpt-5.4-mini，抽出文中明确提到的"开源项目/工具英文名"
  3. 用 GitHub 搜索把名字解析成 owner/repo(取高星最佳匹配)
  4. 去重写入 candidates_douyin.txt，供 github_import.py --add-list 使用

用法：
  python3 mine_douyin.py --limit 80            # 抽前80张提到github的卡 → 写清单
  python3 mine_douyin.py --limit 80 --out candidates_douyin.txt
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
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
CARDS_DIR = Path("/vol1/1000/素材库/星算运营库/素材库/内容素材库/视频卡片")


def load_env():
    cfg = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_env()
GH = CFG.get("GITHUB_TOKEN", "")
RELAY = CFG.get("NEXAI_RELAY_BASE_URL", "https://api.xsai5.xyz/v1").rstrip("/")
RKEY = CFG.get("NEXAI_RELAY_API_KEY", "")
MODEL = CFG.get("NEXAI_RELAY_MODEL", "gpt-5.4-mini")


def chat(prompt, retries=2):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 500}
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(f"{RELAY}/chat/completions",
                                         data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {RKEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
            return json.loads(raw)
        except Exception as e:
            if a < retries:
                time.sleep(3)
            else:
                print(f"[ai] 失败: {e}", file=sys.stderr)
    return None


def gh_search_repo(name):
    """把项目/工具名解析成 owner/repo。用 in:name 精确匹配仓库名(不搜 readme,
    避免命中 awesome 大列表)，并校验仓库名确实包含该词，排除误匹配的 awesome/list 仓。"""
    try:
        q = urllib.parse.urlencode({"q": f"{name} in:name", "sort": "stars", "order": "desc", "per_page": 5})
        h = {"User-Agent": "x", "Accept": "application/vnd.github+json"}
        if GH:
            h["Authorization"] = f"Bearer {GH}"
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/search/repositories?{q}", headers=h), timeout=30) as r:
            items = json.loads(r.read().decode()).get("items", [])
        nlow = re.sub(r"[^a-z0-9]", "", name.lower())
        if len(nlow) < 3:
            return None, 0
        for it in items:
            repo_name = it["full_name"].split("/")[-1]
            rlow = re.sub(r"[^a-z0-9]", "", repo_name.lower())
            if it["stargazers_count"] < 300:
                continue
            if "awesome" in repo_name.lower() and "awesome" not in name.lower():
                continue
            if nlow in rlow or rlow in nlow:  # 名字必须真匹配
                return it["full_name"], it["stargazers_count"]
    except Exception:
        pass
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "candidates_douyin.txt"))
    args = ap.parse_args()

    if not (RKEY and GH):
        print("❌ 缺 NEXAI_RELAY_API_KEY 或 GITHUB_TOKEN", file=sys.stderr)
        sys.exit(1)

    # 找提到 github 的卡，取前 N
    cards = []
    for p in sorted(CARDS_DIR.glob("*.md")):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "github" in txt.lower():
            cards.append((p.name, txt))
        if len(cards) >= args.limit:
            break
    print(f"[mine] 取到 {len(cards)} 张提到 github 的卡，分批抽取项目名...")

    names = set()
    for i in range(0, len(cards), args.batch):
        batch = cards[i:i + args.batch]
        blob = "\n\n".join(f"【卡{j+1}】{c[1][:900]}" for j, c in enumerate(batch))
        prompt = (
            "下面是几段 AI 工具类短视频的文案/转录。请只挑出其中明确提到的「GitHub 开源项目或开源工具」的英文名字"
            "(比如 Claude Code、Cursor、open-webui、dify 这种具体项目名；忽略 ChatGPT/GPT-4 这类闭源产品和泛词)。"
            "输出严格 JSON 数组，元素是项目英文名字符串，最多15个，去重。只输出 JSON 数组。\n\n" + blob[:11000]
        )
        arr = chat(prompt)
        if isinstance(arr, list):
            for n in arr:
                n = str(n).strip()
                if 2 <= len(n) <= 40:
                    names.add(n)
        print(f"  批 {i//args.batch+1}: 累计候选名 {len(names)}")
        time.sleep(0.5)

    print(f"\n[mine] 共 {len(names)} 个候选名，GitHub 解析成 owner/repo ...")
    repos = {}
    for n in sorted(names):
        full, stars = gh_search_repo(n)
        if full:
            repos[full] = max(stars, repos.get(full, 0))
            print(f"  {n} → {full} ⭐{stars}")
        time.sleep(1)

    out = Path(args.out)
    ranked = sorted(repos.items(), key=lambda x: -x[1])
    out.write_text("# 抖音视频抽取的 GitHub 项目\n" + "\n".join(r for r, _ in ranked) + "\n", encoding="utf-8")
    print(f"\n[mine] 解析出 {len(ranked)} 个 owner/repo → 写入 {out}")


if __name__ == "__main__":
    main()
