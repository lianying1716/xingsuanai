"""
文章加工核心模块。
读取公众号案例库中的原始 .md 文件，生成结构化素材卡片，维护 INDEX.md。
所有分析逻辑（爆款要素、反常识钩子、素材评级）均在此模块。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from xingsuanai import config
from xingsuanai.core import categorizer as cat_module

logger = logging.getLogger(__name__)

Callback = Callable[[str], None] | None

# ── 爆款要素检测规则 ──────────────────────────────────────────────────────────

VIRAL_PATTERNS: dict[str, list[str]] = {
    '数字锚点': [
        r'\d+\s*(?:小时|分钟|秒|倍|%|元|块|刀|美元|步|条|个|篇|行|字)',
        r'(?:节省|减少|提升|提高|降低|缩短|增加)\s*\d+',
        r'(?:免费|付费|每月|每年|订阅)\s*\d+',
    ],
    '使用场景': [
        r'(?:适合|可以用来|使用场景|用来|帮你|帮我)',
        r'(?:上班族|学生|自媒体|程序员|设计师|运营|老师|创业者)',
        r'(?:工作|学习|副业|创作|办公|写作|编程|画图)',
    ],
    '操作步骤': [
        r'(?:第[一二三四五六七八九十\d]+步|步骤\s*\d+|\d+\.\s)',
        r'(?:点击|打开|输入|选择|填写|复制|粘贴|安装|下载)',
        r'(?:教程|指南|手把手|保姆级|从零|小白也能)',
    ],
    '价格信息': [
        r'(?:免费|付费|收费|价格|定价|订阅)',
        r'(?:每月|每年|一次性)\s*\d+',
        r'(?:Pro版|会员|高级版|专业版|白嫖|白给)',
        r'(?:人民币|美元|美金|\$|￥|RMB)',
    ],
    '横向对比': [
        r'(?:vs|VS|对比|比较|PK|相比|优于|胜过|不如|区别)',
        r'(?:ChatGPT|Claude|Gemini|DeepSeek|Cursor|Copilot|Kimi|文心)',
        r'(?:哪个更好|谁更强|选哪个|怎么选)',
    ],
    '钩子_否定型': [
        r'(?:你.*一直|大家.*都).*(?:用错|搞错|理解错|弄错)',
        r'(?:千万别|不要再|停止|放弃|别再).*(?:用|买|订阅|付费)',
        r'(?:其实|实际上|真相是).*(?:没那么|并不|不是)',
        r'(?:被高估|被低估|名不副实|虚有其名)',
        r'(?:用了.*才知道|用过.*才发现).*(?:错了|坑|问题)',
    ],
    '钩子_反差型': [
        r'(?:免费|白嫖|零成本).*(?:比|胜过|超过|替代).*(?:付费|Pro|会员)',
        r'(?:小工具|轻量|简单).*(?:比|胜过|干掉|替代).*(?:大厂|主流|知名)',
        r'(?:不用|无需|不花钱).*(?:就能|也能|照样)',
        r'(?:免费版|基础版|lite).*(?:够了|足够|完全可以)',
        r'(?:放弃|退订|不再用).*(?:之后|后来)',
    ],
    '钩子_意外发现型': [
        r'(?:没想到|竟然|居然|出乎意料|意想不到|没料到)',
        r'(?:意外发现|偶然发现|无意中发现)',
        r'(?:隐藏功能|隐藏技巧|冷门用法|鲜为人知|少有人知)',
        r'(?:原来|才知道|才发现).*(?:还能|居然能|竟然可以)',
        r'(?:试了才知道|亲测|实测).*(?:惊了|惊讶|惊喜|超出预期)',
    ],
    '钩子_自白代价型': [
        r'(?:踩了|踩过|吃了|交了).*(?:坑|亏|学费|代价)',
        r'(?:后悔|可惜|遗憾).*(?:没早|早点|早用|早知道)',
        r'(?:用了|试了).*(?:个月|年|周).*(?:感受|体验|总结|复盘)',
        r'(?:血泪|亲身|真实).*(?:教训|经验|总结|体会)',
        r'(?:花了|浪费了).*(?:时间|钱|精力).*(?:才|后)',
    ],
    '钩子_疑问颠覆型': [
        r'(?:真的|到底|究竟).*(?:好用吗|值得吗|有必要吗|靠谱吗)',
        r'(?:为什么|凭什么).*(?:都在用|都转|选择|放弃)',
        r'(?:值不值|要不要|该不该).*(?:订阅|付费|买|升级)',
        r'(?:比.*更好|比.*强|超越|干掉).*(?:\?|？)',
        r'(?:还有人|真有|竟有).*(?:用这个|选这个|付费)',
    ],
}

TOOL_KEYWORDS = [
    'Claude', 'ChatGPT', 'GPT', 'Gemini', 'DeepSeek', 'Kimi', '文心一言',
    'Cursor', 'Copilot', 'Codex', 'Windsurf', 'Claude Code',
    'MCP', 'Agent', 'OpenClaw', 'ComfyUI', 'Midjourney', 'Sora',
    'NotebookLM', 'Perplexity', 'Grok', 'Llama',
    'API', '大模型', '提示词', 'Prompt',
]

# ── 文本清洗 ──────────────────────────────────────────────────────────────────

def _strip_html_noise(text: str) -> str:
    text = re.sub(r'\\([_*\[\]])', r'\1', text)
    text = re.sub(r'#[\w_-]+\s*\{[^}]*\}', '', text)
    text = re.sub(r'\.[\w_-]+\s*\{[^}]*\}', '', text)
    text = re.sub(r'\{[^}]{0,300}\}', '', text)
    text = re.sub(r'\[([^\]]+)\]\(javascript:[^\)]*\)', r'\1', text)
    text = re.sub(r'<[^>]{1,200}>', '', text)
    text = re.sub(r'data-\w+="[^"]*"', '', text)
    text = re.sub(r'class="[^"]*"', '', text)
    text = re.sub(r'style="[^"]*"', '', text)
    lines = [
        l.strip() for l in text.split('\n')
        if l.strip()
        and not re.match(r'^[#\.][a-zA-Z_][\w_-]*\s*$', l.strip())
        and not re.match(r'^(?:max-width|margin|display|width|height|padding):', l.strip())
    ]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _find_article_start(text: str) -> str:
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if re.search(r'[{};]|max-width|margin:|display:|#js_|\.wx_', line):
            continue
        if re.match(r'^[\w-]+:\s*[\w\s,\.\(\)]+;?\s*$', line) and not re.search(r'[\u4e00-\u9fff]', line):
            continue
        if re.search(r'[\u4e00-\u9fff]', line) and len(line) > 5:
            return '\n'.join(lines[i:])
        if line.startswith('#') and len(line) > 3:
            return '\n'.join(lines[i:])
    return text


def extract_text_content(raw: str) -> str:
    clean = _strip_html_noise(raw)
    clean = _find_article_start(clean)
    clean = re.sub(r'!\[.*?\]\(https?://[^\)]+\)', '', clean)
    clean = re.sub(r'^https?://\S+$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'\[([^\]]+)\]\(https?://[^\)]+\)', r'\1', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


# ── 分析函数 ──────────────────────────────────────────────────────────────────

def _parse_filename(fname: str) -> dict:
    stem  = Path(fname).stem
    parts = stem.split('_', 2)
    result = {'date': '', 'account': '', 'title': stem}
    if len(parts) >= 2:
        result['date']    = parts[0] if re.match(r'^\d{8}$', parts[0]) else ''
        result['account'] = parts[1] if len(parts) >= 2 else ''
        result['title']   = parts[2] if len(parts) >= 3 else parts[-1]
    return result


def _detect_tools(text: str) -> list[str]:
    found = [t for t in TOOL_KEYWORDS if t.lower() in text.lower()]
    return list(dict.fromkeys(found))


def _analyze_hooks(text: str) -> dict:
    hook_keys = ['钩子_否定型', '钩子_反差型', '钩子_意外发现型', '钩子_自白代价型', '钩子_疑问颠覆型']
    hits: dict[str, list] = {}
    for hk in hook_keys:
        matches = []
        for pat in VIRAL_PATTERNS.get(hk, []):
            matches.extend(re.findall(pat, text, re.IGNORECASE)[:2])
        if matches:
            hits[hk] = list(dict.fromkeys(matches))

    if not hits:
        return {'strength': 0, 'hook_type': '无', 'found_phrases': [], 'best_sentence': ''}

    strength  = min(len(hits), 3)
    best_type = max(hits, key=lambda k: len(hits[k]))
    all_phrases = [p for ps in hits.values() for p in ps]
    sentence    = _extract_hook_sentence(text, all_phrases)

    return {
        'strength':     strength,
        'hook_type':    best_type.replace('钩子_', ''),
        'found_phrases': all_phrases[:5],
        'best_sentence': sentence,
    }


def _extract_hook_sentence(text: str, phrases: list[str]) -> str:
    sentences = re.split(r'[。！？\n]', text)
    for phrase in phrases:
        for s in sentences:
            s = s.strip()
            if phrase in s and 10 <= len(s) <= 80:
                return s
    return ''


def _detect_viral_elements(text: str) -> dict:
    results = {}
    base_keys = ['数字锚点', '使用场景', '操作步骤', '价格信息', '横向对比']
    for elem in base_keys:
        matches = []
        for pat in VIRAL_PATTERNS.get(elem, []):
            matches.extend(re.findall(pat, text, re.IGNORECASE)[:2])
        results[elem] = {'present': bool(matches), 'examples': list(dict.fromkeys(matches))[:3]}

    hook = _analyze_hooks(text)
    results['反常识钩子'] = {
        'present':       hook['strength'] > 0,
        'examples':      hook['found_phrases'][:3],
        'strength':      hook['strength'],
        'hook_type':     hook['hook_type'],
        'hook_sentence': hook['best_sentence'],
    }
    return results


def _score_material(viral: dict, word_count: int) -> tuple[str, str]:
    present = sum(1 for v in viral.values() if v['present'])
    if present >= 5 and word_count >= 600:
        return 'A', '素材完整，数字/场景/步骤/价格/对比齐备，可直接开写'
    if present >= 3 and (viral.get('数字锚点', {}).get('present') or viral.get('使用场景', {}).get('present')):
        missing = [k for k, v in viral.items() if not v['present']]
        return 'B', f'有核心素材，补充 {", ".join(missing[:2])} 后可写'
    if present >= 1:
        return 'C', '素材稀薄，建议与同类文章合并使用'
    return 'D', '纯资讯，仅作背景参考'


def _generate_hooks(tools: list[str], viral: dict) -> list[dict]:
    main_tool = tools[0] if tools else 'AI工具'
    tool2     = tools[1] if len(tools) > 1 else 'ChatGPT'
    suggs = []
    if viral.get('价格信息', {}).get('present'):
        suggs.append({'type': '反差型', 'hook': f'免费用 {main_tool}，效果不比付费版差',
                      'title': f'我退订了每月 XX 元的会员——{main_tool} 免费版完全够用'})
    if viral.get('横向对比', {}).get('present'):
        suggs.append({'type': '否定型', 'hook': f'你可能一直用错了 {main_tool}（和 {tool2} 的真正区别在这）',
                      'title': f'别再盲目选 {tool2} 了，{main_tool} 在这个场景下强太多'})
    if viral.get('数字锚点', {}).get('present'):
        suggs.append({'type': '意外发现型', 'hook': f'实测 {main_tool} 之后，结果出乎我的意料',
                      'title': f'用了一个月 {main_tool}，数据告诉我一个意外的结论'})
    if viral.get('操作步骤', {}).get('present'):
        suggs.append({'type': '自白代价型', 'hook': f'我踩了 {main_tool} 最常见的坑，教训都在这里',
                      'title': f'新手用 {main_tool} 必看：这几个坑我替你踩过了'})
    if not suggs:
        suggs.append({'type': '疑问颠覆型', 'hook': f'{main_tool} 真的值得学吗？用了才知道',
                      'title': f'所有人都说 {main_tool} 好用，我来说说它的真实体验'})
    return suggs[:3]


def _suggest_titles(tools: list[str], viral: dict) -> list[dict]:
    main_tool = tools[0] if tools else 'AI工具'
    tool2     = tools[1] if len(tools) > 1 else 'ChatGPT'
    hook_info = viral.get('反常识钩子', {})
    suggs = []

    if hook_info.get('present'):
        mapping = {
            '否定型':     f'你可能一直用错了 {main_tool}——真正的高效用法在这',
            '反差型':     f'免费版 {main_tool} 就够了，我退订付费版的理由',
            '意外发现型': f'实测 {main_tool} 一个月，有个结论让我很意外',
            '自白代价型': f'踩了 {main_tool} 这些坑之后，我终于摸清了正确用法',
            '疑问颠覆型': f'{main_tool} 真的值得学吗？用了才有资格说',
        }
        title = mapping.get(hook_info.get('hook_type', ''), f'大家都不知道的 {main_tool} 隐藏用法')
        suggs.append({'angle': f'钩子向（{hook_info["hook_type"]}）', 'title': title, 'platform': '微信/小红书'})

    if viral.get('操作步骤', {}).get('present'):
        suggs.append({'angle': '教程向', 'title': f'不懂技术也能上手：{main_tool} 保姆级入门教程', 'platform': '微信/博客'})
    if viral.get('横向对比', {}).get('present'):
        suggs.append({'angle': '对比向', 'title': f'{main_tool} vs {tool2}：用了一个月，我来说说真实差距', 'platform': '微信/博客'})
    if not suggs:
        suggs.append({'angle': '介绍向', 'title': f'{main_tool} 是什么？普通人能用它来做什么？', 'platform': '微信/博客'})

    return suggs[:3]


def _extract_key_points(text: str) -> list[str]:
    points = []
    for s in re.split(r'[。！？\n]', text):
        s = s.strip()
        if len(s) < 10 or len(s) > 100:
            continue
        if (re.search(r'\d+', s) and len(s) < 60) or re.search(r'(?:vs|VS|比|优于|胜过|不如|区别)', s):
            points.append(s)
        if len(points) >= 5:
            break
    return points


# ── 卡片生成 ──────────────────────────────────────────────────────────────────

def generate_card(raw_path: Path, category: str) -> str:
    raw_text   = raw_path.read_text(encoding='utf-8', errors='replace')
    meta       = _parse_filename(raw_path.name)
    clean_text = extract_text_content(raw_text)
    word_count = len(re.sub(r'\s', '', clean_text))
    tools      = _detect_tools(clean_text)
    viral      = _detect_viral_elements(clean_text)
    grade, reason = _score_material(viral, word_count)
    key_points = _extract_key_points(clean_text)
    titles     = _suggest_titles(tools, viral)
    hook_info  = viral.get('反常识钩子', {})
    hook_strength = hook_info.get('strength', 0)
    generated_hooks = _generate_hooks(tools, viral) if hook_strength == 0 else []

    date_raw = meta.get('date', '')
    pub_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else datetime.now().strftime('%Y-%m-%d')

    try:
        days_old  = (datetime.now() - datetime.strptime(pub_date, '%Y-%m-%d')).days
        freshness = '🟢 新鲜（2周内）' if days_old <= 14 else ('🟡 较新（1个月内）' if days_old <= 30 else '🔴 较旧（超1个月）')
    except Exception:
        freshness = '未知'

    element_rows = ''
    for elem in ['数字锚点', '使用场景', '操作步骤', '价格信息', '横向对比']:
        info = viral.get(elem, {})
        icon = '✅' if info.get('present') else '❌'
        ex   = '、'.join(info.get('examples', [])[:2]) or '-'
        element_rows += f'| {elem} | {icon} | {ex[:30]} |\n'

    hook_bar = '⭐' * hook_strength + '☆' * (3 - hook_strength)
    if hook_strength > 0:
        hook_block = f"""## 反常识钩子分析

**强度：{hook_bar}（{hook_strength}/3）** | **类型：{hook_info["hook_type"]}**

> 检测到的钩子句：「{hook_info.get("hook_sentence") or "（见正文）"}」

✅ 这篇文章自带钩子，可直接提炼为标题核心。"""
    else:
        hook_rows = '\n'.join(f"| {h['type']} | {h['hook']} | {h['title']} |" for h in generated_hooks)
        hook_block = f"""## 反常识钩子分析

**强度：{hook_bar}（0/3）** — 原文未检测到明显钩子

⚠️ 建议写作时主动加入以下钩子角度：

| 钩子类型 | 核心钩子句 | 标题示例 |
|---------|----------|---------|
{hook_rows}

💡 **制造钩子的通用方法：** 找到文章里"读者原本以为 X，但实际上是 Y"的反差点，把 Y 放进标题。"""

    title_rows = ''.join(f'| {t["angle"]} | {t["title"]} | {t["platform"]} |\n' for t in titles)
    key_points_text = '\n'.join(f'- {p}' for p in key_points) or '- （未自动提取到，建议手动阅读原文）'
    preview = clean_text[:300].replace('\n', ' ').strip()

    return f"""---
公众号: {meta.get('account', '未知')}
标题: {meta.get('title', raw_path.stem)}
日期: {pub_date}
分类: {category}
工具: {', '.join(tools[:5]) if tools else '未检测到'}
素材等级: {grade}
钩子强度: {hook_strength}/3
时效性: {freshness}
字数: {word_count}
处理日期: {datetime.now().strftime('%Y-%m-%d')}
原始文件: {raw_path.name}
---

# {meta.get('title', raw_path.stem)}

> 来源：{meta.get('account', '')} | {pub_date} | 字数：{word_count} | **素材等级：{grade}** | 钩子强度：{hook_bar}

## 一句话摘要

{preview[:80]}{'...' if len(preview) > 80 else ''}

（👉 建议阅读原文后手动补充完整摘要）

## 核心素材点（可直接引用）

{key_points_text}

## 爆款要素检测（基础5项）

| 要素 | 是否具备 | 内容示例 |
|------|---------|---------|
{element_rows}
**素材等级：{grade}** — {reason}

{hook_block}

## 时效性评估

{freshness}（发布于 {pub_date}）

## 创作标题建议

| 角度 | 标题思路 | 适合平台 |
|------|---------|---------|
{title_rows}
## 正文预览

{preview}...

---
*由 xingsuanai/processor 自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""


# ── INDEX 维护 ────────────────────────────────────────────────────────────────

def _update_index(entries: list[dict]) -> None:
    config.INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    header = f"""# 公众号素材库 INDEX

> 自动维护 · 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 共 {len(entries)} 篇已加工素材

| 日期 | 公众号 | 标题 | 工具 | 等级 | 分类 |
|------|--------|------|------|------|------|
"""
    rows = ''.join(
        f"| {e.get('date','')} | {e.get('account','')} | {e.get('title','')[:25]} "
        f"| {e.get('tools','')[:20]} | **{e.get('grade','')}** | {e.get('category','')} |\n"
        for e in sorted(entries, key=lambda x: x.get('date', ''), reverse=True)
    )
    config.INDEX_FILE.write_text(header + rows, encoding='utf-8')


# ── 状态管理 ──────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if config.PROCESS_STATE.exists():
        try:
            return json.loads(config.PROCESS_STATE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'processed': {}, 'last_run': None}


def _save_state(state: dict) -> None:
    config.PROCESS_STATE.parent.mkdir(parents=True, exist_ok=True)
    config.PROCESS_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run(rebuild: bool = False, single_file: str | None = None,
        progress_cb: Callback = None) -> dict:
    """
    处理未加工文章，生成素材卡片。

    rebuild=True 时清除已处理记录，全量重建。
    single_file 指定单篇文章路径时只处理该文件。
    progress_cb 每条消息回调。
    """

    def emit(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    state = _load_state()
    if rebuild:
        state['processed'] = {}
        emit('重建模式：清除已处理记录')

    processed = state['processed']

    if single_file:
        raw_path = Path(single_file)
        raw_files = [(raw_path, raw_path.parent.name)] if raw_path.exists() else []
    else:
        raw_files = []
        if config.RAW_DIR.exists():
            for cat_dir in config.RAW_DIR.iterdir():
                if cat_dir.is_dir():
                    for f in cat_dir.glob('*.md'):
                        if str(f) not in processed or rebuild:
                            raw_files.append((f, cat_dir.name))

    if not raw_files:
        emit('没有需要处理的文章')
        return {'ok': True, 'processed': 0, 'skipped': len(processed)}

    emit(f'待处理：{len(raw_files)} 篇')
    all_entries = list(processed.values())
    done = 0

    for raw_path, category in raw_files:
        emit(f'  处理: {raw_path.name[:60]}')
        try:
            card = generate_card(raw_path, category)
            card_dir = config.CARD_DIR / category
            card_dir.mkdir(parents=True, exist_ok=True)
            (card_dir / raw_path.name).write_text(card, encoding='utf-8')

            meta     = _parse_filename(raw_path.name)
            date_raw = meta.get('date', '')
            pub_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else ''
            clean    = extract_text_content(raw_path.read_text(encoding='utf-8', errors='replace'))
            tools    = _detect_tools(clean)
            viral    = _detect_viral_elements(clean)
            grade, _ = _score_material(viral, len(re.sub(r'\s', '', clean)))

            entry = {
                'date':     pub_date,
                'account':  meta.get('account', ''),
                'title':    meta.get('title', raw_path.stem)[:30],
                'tools':    ', '.join(tools[:3]),
                'grade':    grade,
                'category': category,
                'card':     str(card_dir / raw_path.name),
            }
            processed[str(raw_path)] = entry
            all_entries.append(entry)
            done += 1
            emit(f'    → 等级 {grade} | {", ".join(tools[:2])}')
        except Exception as e:
            emit(f'    [ERROR] {e}')

    state['last_run'] = datetime.now().isoformat()
    _save_state(state)
    _update_index(all_entries)

    emit(f'处理完成：{done} 篇，卡片保存于 {config.CARD_DIR}')
    return {'ok': True, 'processed': done, 'skipped': len(processed) - done}


def get_stats() -> dict:
    """返回素材库统计数据。"""
    state     = _load_state()
    processed = state.get('processed', {})
    raw_count = sum(1 for _ in config.RAW_DIR.rglob('*.md')) if config.RAW_DIR.exists() else 0
    grades    = {}
    cats      = {}
    for e in processed.values():
        g = e.get('grade', '?')
        c = e.get('category', '?')
        grades[g] = grades.get(g, 0) + 1
        cats[c]   = cats.get(c, 0) + 1

    return {
        'raw_articles':  raw_count,
        'processed':     len(processed),
        'pending':       raw_count - len(processed),
        'grades':        grades,
        'categories':    cats,
        'last_run':      state.get('last_run'),
    }
