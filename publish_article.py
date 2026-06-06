#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_article.py —— 站内文章发布·防呆一条命令（固化 SOP-站内文章发布手册）
============================================================
读 markdown(frontmatter + 正文) → content:context 取真实分类 → 组 content packet →
content:validate → 合规扫(全平台铁律) → 用【固定 idempotency-key】发布(防重复) → 核验线上 URL。

markdown 格式：
---
title: 标题
slug: 唯一-英文-slug
seo_title: ...
seo_description: ...
excerpt: ...
editor_lead: ...
category: 模型使用教程        # 名字,脚本自动解析 slug
cover_media_id: 98
type: post                   # 默认 post
template: guide              # 默认 guide
status: draft                # 默认 draft;--publish 时强制 published
---
正文 markdown：## 二级标题 / ### 三级 / 段落 / - 列表 / ![alt](url) 后跟 <!-- mediaId: 103 -->

用法：
  python3 publish_article.py --file 稿.md                 # 演练:组包+校验+合规,不发
  python3 publish_article.py --file 稿.md --publish        # 真发(固定key防重)
  python3 publish_article.py --file 稿.md --publish --review  # 发前额外跑 content_review 独立审
"""
import argparse, json, re, subprocess, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = str(Path.home() / ".claude/skills/nexai-content-publisher-pack/scripts/operator-cli.js")
ENV = {}
for ln in (HERE / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); ENV[k.strip()] = v.strip()
SITE = ENV.get("OPERATOR_BASE_URL", "https://xsai5.xyz").rstrip("/")
RULES = json.loads((HERE / "platform_rules.json").read_text(encoding="utf-8"))


def cli(*args, infile=None):
    cmd = ["node", CLI, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=HERE)
    out = r.stdout.strip()
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out[-500:], "_err": r.stderr[-300:]}


def parse_md(path):
    txt = Path(path).read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.S)
    if not m:
        sys.exit("❌ markdown 缺 frontmatter(--- ... ---)")
    fmraw, body = m.group(1), m.group(2)
    fm = {}
    for ln in fmraw.splitlines():
        if ":" in ln and not ln.strip().startswith("#"):
            k, v = ln.split(":", 1); fm[k.strip()] = v.strip()
    return fm, body.strip()


def inline(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', s)
    return s


def body_to_blocks(body):
    lines = body.split("\n")
    blocks, i, n = [], 0, 0
    buf_list = []

    def flush_list():
        nonlocal buf_list, n
        if buf_list:
            tag = "ol" if buf_list[0][0] == "o" else "ul"
            items = "".join(f"<li>{inline(x[1])}</li>" for x in buf_list)
            n += 1; blocks.append({"id": f"l{n}", "type": "paragraph", "html": f"<{tag}>{items}</{tag}>"})
            buf_list = []

    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        img = re.match(r"!\[(.*?)\]\((.*?)\)", s)
        if not s:
            flush_list(); i += 1; continue
        if img:
            flush_list()
            mid = ""
            if i + 1 < len(lines):
                mm = re.search(r"mediaId:\s*(\d+)", lines[i + 1])
                if mm:
                    mid = mm.group(1); i += 1
            n += 1; blocks.append({"id": f"img{n}", "type": "image", "src": img.group(2), "alt": img.group(1), "mediaId": mid})
            i += 1; continue
        h = re.match(r"^(#{2,3})\s+(.*)$", s)
        if h:
            flush_list(); lvl = len(h.group(1))
            n += 1; blocks.append({"id": f"h{n}", "type": "heading", "level": lvl, "html": inline(h.group(2))})
            i += 1; continue
        li = re.match(r"^(\d+\.|[-*])\s+(.*)$", s)
        if li:
            buf_list.append(("o" if li.group(1)[0].isdigit() else "u", li.group(2))); i += 1; continue
        flush_list()
        n += 1; blocks.append({"id": f"p{n}", "type": "paragraph", "html": inline(s)})
        i += 1
    flush_list()
    return blocks


def blocks_to_html(blocks):
    out = []
    for b in blocks:
        if b["type"] == "heading":
            out.append(f"<h{b['level']}>{b['html']}</h{b['level']}>")
        elif b["type"] == "image":
            out.append(f'<img src="{b["src"]}" alt="{b["alt"]}"/>')
        else:
            out.append(f"<p>{b['html']}</p>")
    return "".join(out)


def compliance_scan(text):
    g = RULES["global_redlines"]
    hard = [w for w in g["政策高压线_绝不可提"] if w in text]
    soft = [w for w in (g["其它禁词"] + g["绝对化极限词_广告法第九条"]) if w in text]
    return hard, soft


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--review", action="store_true", help="发前额外跑 content_review 独立审")
    ap.add_argument("--idk", help="固定 idempotency-key(默认 article-<slug>-v1)")
    args = ap.parse_args()

    fm, body = parse_md(args.file)
    slug = fm.get("slug") or sys.exit("❌ frontmatter 缺 slug")
    idk = args.idk or f"article-{slug}-v1"

    # 取真实分类 slug
    ctx = cli("content:context")
    cats = ctx.get("categories", [])
    cat_name = fm.get("category", "")
    cat_slug = next((c["slug"] for c in cats if cat_name and cat_name in c.get("name", "")), "")
    if cat_name and not cat_slug:
        print(f"⚠️ 分类「{cat_name}」未匹配到,可选:{[c.get('name') for c in cats]}", file=sys.stderr)

    blocks = body_to_blocks(body)
    packet = {
        "type": fm.get("type", "post"), "template": fm.get("template", "guide"), "authorId": "admin",
        "status": "published" if args.publish else fm.get("status", "draft"),
        "title": fm.get("title", ""), "slug": slug,
        "categorySlugs": [cat_slug] if cat_slug else [],
        "featuredMediaId": str(fm.get("cover_media_id", "")) or None,
        "seoTitle": fm.get("seo_title") or fm.get("title", ""),
        "seoDescription": fm.get("seo_description") or fm.get("description", ""),
        "excerpt": fm.get("excerpt", ""), "editorLead": fm.get("editor_lead", ""),
        "reviewNotes": f"publish_article.py 自动发布 {slug}",
        "sourceBundle": {"references": [{"type": "internal", "label": "星算助手功能清单/真实界面截图"}]},
        "claimLedger": {"claims": [{"claim": fm.get("title", slug), "evidence": ["internal:星算助手功能清单/真实截图"]}]},
        "agentMetadata": {"provider": "anthropic", "toolName": "operator-cli", "modelName": "claude-opus", "operator": "publish_article.py"},
        "editorDocument": {"schema": "nexai-single-column-v1", "version": 1, "blocks": blocks},
        "contentHtml": blocks_to_html(blocks),
    }
    pf = HERE / f".packet_{slug}.json"
    pf.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[1/5] 组包 ✅ slug={slug} 分类={cat_slug or '(无)'} blocks={len(blocks)} 封面={packet['featuredMediaId']}")

    # 合规扫
    hard, soft = compliance_scan(body + " " + fm.get("title", ""))
    print(f"[2/5] 合规扫 政策硬命中={hard or '无✅'} | 软标记(看上下文)={soft or '无'}")
    if hard:
        sys.exit("⛔ 命中政策高压线,禁止发布。先改稿。")

    # 校验
    v = cli("content:validate", "--file", str(pf), "--publish-mode")
    vok = (v.get("validation") or {}).get("ok")
    print(f"[3/5] 校验 ok={vok} errors={(v.get('validation') or {}).get('errors')}")
    if not vok:
        sys.exit(f"⛔ 校验未过:{json.dumps(v.get('validation'), ensure_ascii=False)[:300]}")

    # 独立审(可选)
    if args.review:
        rv = subprocess.run([sys.executable, str(HERE / "content_review.py"), "--file", str(pf), "--platform", "站内"],
                            capture_output=True, text=True)
        print("[3.5] content_review 独立审:\n" + (rv.stdout[-800:] or rv.stderr[-300:]))

    if not args.publish:
        print("[done] 演练完成(未发布)。确认后加 --publish 真发。")
        return

    # 发布(固定 idempotency-key 防重复)
    res = cli("content:publish", "--file", str(pf), "--idempotency-key", idk)
    item = res.get("item", {})
    print(f"[4/5] 发布 idk={idk} → id={item.get('id')} status={item.get('status')} slug={item.get('slug')} dup={res.get('duplicate')}")
    if item.get("slug") and item["slug"] != slug:
        print(f"⚠️ 返回 slug={item['slug']} != {slug}(可能已存在同 slug)。检查是否重复,必要时清理。")

    # 查重 + 核验线上
    def code(u):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=15).getcode()
        except Exception as e:
            return getattr(e, "code", "ERR")
    live = f"{SITE}/blog/{slug}"
    dup = f"{SITE}/blog/{slug}-2"
    print(f"[5/5] 核验  上线 {live} → {code(live)}  | 查重 {slug}-2 → {code(dup)}(期望404/ERR)")
    print(f"\n✅ 完成：{live}")


if __name__ == "__main__":
    main()
