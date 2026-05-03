"""
抖音视频内容处理模块。
NFO 解析 + 卡片生成 + 索引生成运行在容器内（纯 Python）。
Whisper 转录通过 subprocess 调用挂载的宿主机 .venv（已含 faster-whisper）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from xingsuanai import config

logger = logging.getLogger(__name__)

Callback = Callable[[str], None] | None

# ── 路径 ──────────────────────────────────────────────────────────────────────
DOUYIN_DIR   = config.BASE_DIR / '素材库/抖音案例库'
OUTPUT_DIR   = config.BASE_DIR / '素材库/内容素材库'
CARDS_DIR    = OUTPUT_DIR / '视频卡片'
CACHE_FILE   = OUTPUT_DIR / '.transcript_cache.json'
DOUYIN_STATE = OUTPUT_DIR / '.douyin_state.json'

AI_TOOLS = [
    'Claude', 'ChatGPT', 'GPT-4', 'GPT-4o', 'Gemini', 'Grok', 'Midjourney',
    'Stable Diffusion', 'DALL-E', 'Sora', 'Cursor', 'Copilot', 'Windsurf',
    'Perplexity', 'Claude Code', 'Claude Desktop', 'MCP', 'ComfyUI',
    'n8n', 'Make', 'Zapier', 'Dify', 'LangChain', 'Codex', 'DeepSeek',
]

# Whisper 配置（用于 subprocess 调用脚本）
WHISPER_SCRIPT = config.BASE_DIR / '素材库/extract_whisper.py'


# ── NFO 解析 ──────────────────────────────────────────────────────────────────

def _parse_nfo(nfo_path: Path) -> dict:
    try:
        raw = nfo_path.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        tree = ET.fromstring(raw.decode('utf-8'))
    except Exception as e:
        logger.warning('NFO 解析失败 %s: %s', nfo_path.name, e)
        return {}

    title_full = tree.findtext('title', '').strip()
    clean      = re.sub(r'\n?#\S+', '', title_full).strip()

    DESC_MARKERS = ['这段视频', '本视频', '视频中', '该视频', '在这个', '视频介绍',
                    '本期', '这期', '在本', '作者', '主播', '博主介绍']
    title, summary = clean, ''
    for marker in DESC_MARKERS:
        idx = clean.find(marker)
        if 0 < idx < 60:
            title, summary = clean[:idx].strip(), clean[idx:].strip()
            break

    if not summary:
        parts = clean.split('\n', 1)
        if len(parts) == 2:
            title, summary = parts[0].strip(), parts[1].strip()
        elif len(clean) > 50:
            m = re.search(r'[。！？!?]', clean[:60])
            if m:
                title, summary = clean[:m.end()].strip(), clean[m.end():].strip()
            else:
                title, summary = clean[:40].strip(), clean[40:].strip()

    hashtags = re.findall(r'#(\S+)', title_full)
    actor    = tree.find('actor/name')
    author   = actor.text.strip() if actor is not None else nfo_path.parent.parent.name
    genres   = [g.text.strip() for g in tree.findall('genre') if g.text]
    date     = tree.findtext('releasedate', '') or tree.findtext('premiered', '')

    return {
        'author': author, 'date': date, 'title': title, 'summary': summary,
        'hashtags': hashtags, 'genres': genres, 'nfo_path': str(nfo_path),
    }


# ── 卡片生成 ──────────────────────────────────────────────────────────────────

def _extract_tools(text: str) -> list[str]:
    tl = text.lower()
    return [t for t in AI_TOOLS if t.lower() in tl]


def _gen_angles(meta: dict, tools: list[str]) -> list[dict]:
    angles  = []
    tags_l  = [t.lower() for t in meta.get('hashtags', [])]
    tool0   = tools[0] if tools else 'AI'

    if tools:
        angles.append({'角度': '工具测评',
                       '标题思路': f'{tool0} 深度测评：真实使用体验和避坑指南',
                       '适合平台': '博客'})
    if len(tools) >= 2:
        angles.append({'角度': '对比分析',
                       '标题思路': f'{tools[0]} vs {tools[1]}：谁更值得订阅？',
                       '适合平台': '博客/微信'})
    if any(k in tags_l for k in ['教程', '技巧', '入门', 'code', '编程']):
        angles.append({'角度': '实操教程',
                       '标题思路': f'手把手教你用好 {tool0}，附完整操作步骤',
                       '适合平台': '微信/博客'})
    if any(k in tags_l for k in ['效率', '工作流', '自动化', '办公']):
        angles.append({'角度': '效率干货',
                       '标题思路': f'用 {tool0} 每天节省2小时，这套工作流免费抄',
                       '适合平台': '小红书/微信'})
    if not angles:
        angles.append({'角度': '行业洞察',
                       '标题思路': f'关于 {meta.get("title", "")[:20]}，我有话说',
                       '适合平台': '博客'})
    return angles


def _gen_card(meta: dict, transcript: str) -> str:
    tools      = _extract_tools(meta.get('title', '') + ' ' + meta.get('summary', '') + ' ' + transcript)
    angles     = _gen_angles(meta, tools)
    tags_str   = ' '.join(f'`#{t}`' for t in meta.get('hashtags', []))
    genres_str = ' / '.join(meta.get('genres', []))
    angle_tbl  = '| 角度 | 标题思路 | 适合平台 |\n|---|---|---|\n'
    for a in angles:
        angle_tbl += f"| {a['角度']} | {a['标题思路']} | {a['适合平台']} |\n"
    tools_str = '、'.join(tools) if tools else '（未检测到）'
    ts_text   = transcript.strip() if transcript else '（转录进行中，稍后自动填充）'

    return f"""---
博主: {meta.get('author', '')}
日期: {meta.get('date', '')}
平台: 抖音
标签: {tags_str}
类别: {genres_str}
---

# {meta.get('title', '（无标题）')}

## NFO 摘要

{meta.get('summary', '（无摘要）')}

## 完整文案（Whisper 转录）

{ts_text}

## 关键要素

- **工具/产品**: {tools_str}
- **话题标签**: {', '.join(meta.get('hashtags', [])) or '无'}
- **内容类别**: {genres_str}

## 文章创作建议

{angle_tbl}""".strip()


def _safe_fn(text: str, n: int = 40) -> str:
    return re.sub(r'[\\/:*?"<>|#\s]', '_', text)[:n].rstrip('_')


# ── 索引生成 ──────────────────────────────────────────────────────────────────

def _gen_index(all_meta: list[dict]) -> None:
    lines = [
        '# 星算AI博客 · 内容素材库', '',
        f'> 更新：{datetime.now().strftime("%Y-%m-%d %H:%M")}  '
        f'视频：{len(all_meta)} | 博主：{len(set(m["author"] for m in all_meta))}',
        '', '---', '', '## 导航', '',
        '- [选题库](选题库.md)', '- [热词分析](热词分析.md)',
        '- [内容地图](内容地图.md)', '- [视频卡片/](视频卡片/)',
        '', '---', '', '## 所有视频（按日期倒序）', '',
        '| 日期 | 博主 | 标题 | 标签 |', '|---|---|---|---|',
    ]
    for m in sorted(all_meta, key=lambda x: x.get('date', ''), reverse=True):
        t    = m.get('title', '')[:35] + ('…' if len(m.get('title', '')) > 35 else '')
        tags = ' '.join(f'#{x}' for x in m.get('hashtags', [])[:3])
        lines.append(f"| {m.get('date', '')} | {m.get('author', '')} | {t} | {tags} |")
    (OUTPUT_DIR / 'INDEX.md').write_text('\n'.join(lines), encoding='utf-8')


def _gen_topics(all_meta: list[dict]) -> None:
    tag_c = Counter(t.lower() for m in all_meta for t in m.get('hashtags', []))
    lines = ['# 选题库', '', f'> 基于 {len(all_meta)} 个视频的话题统计', '',
             '| 排名 | 话题 | 频次 |', '|---|---|---|']
    for i, (tag, c) in enumerate(tag_c.most_common(20), 1):
        lines.append(f'| {i} | #{tag} | {c} |')
    lines += ['', '## 可写选题', '']
    for m in sorted(all_meta, key=lambda x: x.get('date', ''), reverse=True):
        if m.get('title'):
            lines.append(f"- **[{m['author']}]** {m.get('title', '')[:60]}")
    (OUTPUT_DIR / '选题库.md').write_text('\n'.join(lines), encoding='utf-8')


def _gen_keywords(all_meta: list[dict]) -> None:
    tags   = Counter(t.lower() for m in all_meta for t in m.get('hashtags', []))
    genres = Counter(g for m in all_meta for g in m.get('genres', []))
    tools  = Counter(t for m in all_meta
                     for t in _extract_tools(m.get('title', '') + m.get('summary', '')))
    lines  = ['# 热词分析', '', '## 标签 TOP 30', '', '| 标签 | 频次 |', '|---|---|']
    for tag, c in tags.most_common(30):
        lines.append(f'| #{tag} | {c} |')
    lines += ['', '## 工具统计', '', '| 工具 | 次数 |', '|---|---|']
    for t, c in tools.most_common(15):
        lines.append(f'| {t} | {c} |')
    lines += ['', '## 内容类别', '', '| 类别 | 视频数 |', '|---|---|']
    for g, c in genres.most_common():
        lines.append(f'| {g} | {c} |')
    (OUTPUT_DIR / '热词分析.md').write_text('\n'.join(lines), encoding='utf-8')


def _gen_map(all_meta: list[dict]) -> None:
    av = Counter(m['author'] for m in all_meta)
    at: dict[str, set] = {}
    for m in all_meta:
        at.setdefault(m['author'], set()).update(m.get('hashtags', []))
    lines = ['# 内容地图', '', '| 博主 | 视频数 | 主要话题 |', '|---|---|---|']
    for author, cnt in av.most_common():
        tags = ' '.join(f'#{t}' for t in list(at.get(author, set()))[:5])
        lines.append(f'| {author} | {cnt} | {tags} |')
    lines += [
        '', '## 差异化机会点', '',
        '1. **订阅决策辅助** — 帮读者回答"值不值得买"，直接对接 xsai5.xyz',
        '2. **避坑系列** — 真实踩坑经验，竞品几乎没有',
        '3. **中文用户视角** — 访问限制、支付、使用场景',
        '4. **企业/团队场景** — 面向中小企业，几乎无竞争',
    ]
    (OUTPUT_DIR / '内容地图.md').write_text('\n'.join(lines), encoding='utf-8')


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run(progress_cb: Callback = None) -> dict:
    """
    NFO 解析 + 卡片生成 + 索引更新（不含 Whisper 转录）。
    直接在容器内运行，读取已有转录缓存。
    """

    def emit(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)

    if not DOUYIN_DIR.exists():
        return {'ok': False, 'error': f'抖音案例库目录不存在: {DOUYIN_DIR}'}

    nfo_files = sorted(DOUYIN_DIR.rglob('*.nfo'))
    mp4_files = sorted(DOUYIN_DIR.rglob('*.mp4'))
    emit(f'找到 {len(nfo_files)} NFO，{len(mp4_files)} 视频')

    # 读取已有转录缓存（由宿主机 Whisper 生成）
    cache: dict[str, str] = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    emit(f'转录缓存：{len(cache)} 条')

    all_meta: list[dict] = []
    for nfo in nfo_files:
        meta = _parse_nfo(nfo)
        if meta:
            mp4 = next((p for p in mp4_files if p.parent == nfo.parent), None)
            meta['mp4_path'] = str(mp4) if mp4 else ''
            all_meta.append(meta)

    emit(f'解析完成：{len(all_meta)} 条 NFO 记录')

    done = 0
    for m in all_meta:
        transcript = cache.get(m.get('mp4_path', ''), '')
        card = _gen_card(m, transcript)
        fn   = _safe_fn(f"{m.get('author', 'x')}_{m.get('title', '')[:20]}")
        (CARDS_DIR / f'{fn}.md').write_text(card, encoding='utf-8')
        done += 1

    _gen_index(all_meta)
    _gen_topics(all_meta)
    _gen_keywords(all_meta)
    _gen_map(all_meta)

    state = {'last_run': datetime.now().isoformat(), 'cards': done, 'transcribed': len(cache)}
    DOUYIN_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

    emit(f'完成：{done} 张卡片，{len(cache)} 条已转录')
    return {'ok': True, 'cards': done, 'transcribed': len(cache), 'total_nfo': len(nfo_files)}


def run_transcription(progress_cb: Callback = None) -> dict:
    """
    调用宿主机 .venv 中的 faster-whisper 进行转录。
    通过 subprocess 运行挂载的宿主机 Python，不在容器内安装 Whisper 模型。
    """

    def emit(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    venv_py = config.WECHAT_PY  # /opt/wechat-venv/bin/python3（宿主 .venv）
    nas_script = str(config.BASE_DIR / '../../../extract_nas.py')  # 宿主机原始脚本

    # 使用一个内联 Python 脚本调用 Whisper，避免路径依赖
    inline = f"""
import sys
sys.path.insert(0, '{config.BASE_DIR}')
import json, re
from pathlib import Path
from datetime import datetime

DOUYIN_DIR = Path('{DOUYIN_DIR}')
CACHE_FILE = Path('{CACHE_FILE}')
WHISPER_MODEL   = 'large-v3'
WHISPER_DEVICE  = 'cpu'
WHISPER_COMPUTE = 'int8'
WHISPER_LANG    = 'zh'
WHISPER_BEAM    = 5

from faster_whisper import WhisperModel

cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {{}}
mp4_files = sorted(DOUYIN_DIR.rglob('*.mp4'))
new_count = 0

model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
for mp4 in mp4_files:
    key = str(mp4)
    if key in cache:
        continue
    print(f'[转录] {{mp4.name}}', flush=True)
    try:
        segs, info = model.transcribe(
            str(mp4), language=WHISPER_LANG, beam_size=WHISPER_BEAM,
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500),
        )
        text = ' '.join(s.text.strip() for s in segs)
        cache[key] = text
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        new_count += 1
        print(f'[done] {{info.duration:.0f}}s -> {{len(text)}} 字', flush=True)
    except Exception as e:
        print(f'[error] {{e}}', flush=True)

print(f'转录完成：新增 {{new_count}} 条', flush=True)
"""

    emit('启动 Whisper 转录（使用宿主机 .venv）...')
    logs = []
    try:
        proc = subprocess.Popen(
            [venv_py, '-c', inline],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=os.environ.copy(),
        )
        for line in proc.stdout:  # type: ignore
            line = line.rstrip()
            emit(line)
            logs.append(line)
        proc.wait(timeout=3600)  # 最长 1 小时
    except subprocess.TimeoutExpired:
        proc.kill()
        return {'ok': False, 'error': '转录超时（>1h）', 'log': logs}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'log': logs}

    # 转录完成后重新生成卡片
    emit('转录完成，重新生成卡片...')
    run(progress_cb=progress_cb)

    return {'ok': True, 'log': logs}


def get_stats() -> dict:
    state: dict = {}
    if DOUYIN_STATE.exists():
        try:
            state = json.loads(DOUYIN_STATE.read_text(encoding='utf-8'))
        except Exception:
            pass

    nfo_count = len(list(DOUYIN_DIR.rglob('*.nfo'))) if DOUYIN_DIR.exists() else 0
    mp4_count = len(list(DOUYIN_DIR.rglob('*.mp4'))) if DOUYIN_DIR.exists() else 0
    card_count = len(list(CARDS_DIR.glob('*.md'))) if CARDS_DIR.exists() else 0

    cache_count = 0
    if CACHE_FILE.exists():
        try:
            cache_count = len(json.loads(CACHE_FILE.read_text(encoding='utf-8')))
        except Exception:
            pass

    return {
        'nfo_files':   nfo_count,
        'mp4_files':   mp4_count,
        'video_cards': card_count,
        'transcribed': cache_count,
        'pending_transcription': max(0, mp4_count - cache_count),
        'last_run':    state.get('last_run'),
    }
