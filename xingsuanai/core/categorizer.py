"""
动态分类器 — 根据文章内容自动归类，不依赖账号配置里的硬编码 category。
每篇文章按内容关键词评分，取最高分分类，未命中则归入 AI效率工具。
"""

from __future__ import annotations

# 分类规则：关键词列表，支持大小写混合
RULES: dict[str, list[str]] = {
    'Claude工具教程': [
        'Claude Desktop', 'claude desktop', 'Claude Code', 'claude code',
        'Anthropic', 'MCP server', 'MCP协议', 'Claude API', 'claude api',
        'Projects功能', 'Artifacts',
    ],
    'AI编程开发': [
        'Cursor', 'Copilot', 'Codex', 'Windsurf', 'GitHub Copilot',
        '编程', '代码', '开发者', '程序员', '开源项目', 'GitHub项目',
        'Claude Code', 'Aider', 'Devin', 'SWE-bench',
    ],
    'AI绘图创作': [
        'Midjourney', 'ComfyUI', 'Stable Diffusion', 'DALL-E', 'Sora',
        'AI绘画', 'AI图像', '文生图', '图生图', 'LoRA', 'Flux',
        'Leonardo', 'Ideogram',
    ],
    '行业资讯动态': [
        '发布会', '正式发布', '重磅更新', '融资', '估值', '收购', '裁员',
        'OpenAI宣布', 'Google发布', '微软发布', '新版本', '大模型发布',
        'GPT-5', 'Gemini Ultra', '行业报告',
    ],
    'AI效率工具': [
        '效率', '工作流', '自动化', 'Agent', '智能体',
        'ChatGPT', 'Gemini', 'DeepSeek', 'Kimi', '文心一言',
        'Perplexity', 'NotebookLM', 'OpenClaw', '提示词', 'Prompt',
        'n8n', 'Make', 'Zapier',
    ],
}

# 各分类的基础权重（用于平局时的优先级）
_PRIORITY = ['Claude工具教程', 'AI编程开发', 'AI绘图创作', '行业资讯动态', 'AI效率工具']
_DEFAULT  = 'AI效率工具'


def classify(text: str) -> str:
    """对文章文本打分并返回最佳分类名称。"""
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for cat, keywords in RULES.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        scores[cat] = score

    max_score = max(scores.values())
    if max_score == 0:
        return _DEFAULT

    # 同分时按优先级顺序取第一个
    for cat in _PRIORITY:
        if scores.get(cat, 0) == max_score:
            return cat

    return _DEFAULT


def list_categories() -> list[str]:
    """返回所有已定义的分类名称（有序）。"""
    return list(_PRIORITY)
