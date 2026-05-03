# 星算AI 内容管道

FastAPI 服务，运行在 NAS Docker 中，统一管理公众号监控、文章加工、抖音素材处理。

端口 **7800**，内置 APScheduler 自动调度（无需 cron）。

---

## 快速部署

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/xingsuanai.git
cd xingsuanai

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入实际的 auth key 和代理密码

# 3. 启动
docker compose up -d

# 4. 查看日志
docker compose logs -f
```

服务启动后访问 `http://NAS_IP:7800/docs` 查看完整 API 文档。

---

## 目录结构

```
xingsuanai/
├── xingsuanai/
│   ├── api/
│   │   ├── main.py            # FastAPI 入口，lifespan 启动调度器
│   │   └── routers/
│   │       ├── pipeline.py    # 公众号 monitor/process
│   │       ├── materials.py   # 素材库浏览
│   │       ├── accounts.py    # 监控账号管理
│   │       └── douyin.py      # 抖音 NFO/转录
│   └── core/
│       ├── config.py          # 路径与环境变量统一配置
│       ├── categorizer.py     # 动态内容分类
│       ├── monitor.py         # 公众号监控抓取
│       ├── processor.py       # 文章加工 → 素材卡片
│       ├── douyin.py          # NFO 解析 + Whisper 转录
│       └── scheduler.py      # APScheduler 封装
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## 调度时间（Asia/Shanghai）

| 任务 | 时间 |
|------|------|
| 公众号监控抓取 | 每天 02:17 |
| 文章加工处理 | 每天 02:47 |
| 抖音 NFO 处理 | 每天 03:00 |

---

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/status` | 整体状态 |
| GET | `/health` | 健康检查 |
| POST | `/pipeline/monitor` | 手动触发公众号监控 |
| POST | `/pipeline/process` | 手动触发文章加工 |
| GET | `/pipeline/jobs/{id}` | 查询任务进度 |
| POST | `/douyin/process` | 重新解析 NFO |
| POST | `/douyin/transcribe` | 触发 Whisper 转录 |
| GET | `/douyin/stats` | 抖音素材统计 |

---

## 环境变量说明

| 变量 | 说明 |
|------|------|
| `XINGSUANAI_DATA_DIR` | 数据目录（docker-compose 已设置） |
| `WECHAT_ARTICLE_AUTH_KEY` | wechat-article-exporter auth key |
| `WECHAT_ARTICLE_PROXY_URLS` | Cloudflare Worker 代理地址 |
| `WECHAT_ARTICLE_PROXY_AUTH` | 代理密码 |

## Volume 挂载要求

- `/vol1/1000/素材库` → `/data/素材库`：素材数据目录
- `/home/ztb/wechat-skill` → `/opt/wechat-skill:ro`：公众号采集脚本
- `/home/ztb/.venv` → `/opt/wechat-venv:ro`：宿主机 Python 虚拟环境（含 faster-whisper）
