"""
星算AI 内容管道 — FastAPI 主入口。
端口 7800，内置 APScheduler 自动调度 monitor / process。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xingsuanai import config
from xingsuanai.api.routers import accounts, douyin, materials, pipeline
from xingsuanai.core import douyin as douyin_core, monitor, processor, scheduler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('星算AI 内容管道启动中...')
    logger.info('DATA_DIR: %s', config.BASE_DIR)
    scheduler.start(
        monitor_fn=lambda: monitor.run(),
        process_fn=lambda: processor.run(),
        douyin_fn=lambda: douyin_core.run(),
    )
    yield
    scheduler.stop()
    logger.info('星算AI 内容管道已停止')


app = FastAPI(
    title='星算AI 内容管道',
    description='公众号监控 · 文章加工 · 素材管理',
    version='1.0.0',
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(pipeline.router,  prefix='/pipeline',  tags=['Pipeline'])
app.include_router(materials.router, prefix='/materials', tags=['Materials'])
app.include_router(accounts.router,  prefix='/accounts',  tags=['Accounts'])
app.include_router(douyin.router,    prefix='/douyin',    tags=['Douyin'])


@app.get('/status', tags=['System'], summary='整体管道状态')
def get_status():
    import json
    monitor_state = {}
    if config.MONITOR_STATE.exists():
        try:
            monitor_state = json.loads(config.MONITOR_STATE.read_text(encoding='utf-8'))
        except Exception:
            pass

    stats  = processor.get_stats()
    dstats = douyin_core.get_stats()
    accs   = config.load_accounts_config()

    return {
        'ok':               True,
        'version':          '1.0.0',
        'wechat': {
            'raw_articles':   stats['raw_articles'],
            'processed_cards': stats['processed'],
            'pending':        stats['pending'],
            'grade_distribution': stats['grades'],
            'last_monitor':   monitor_state.get('last_run'),
            'last_process':   stats.get('last_run'),
        },
        'douyin': {
            'nfo_files':    dstats['nfo_files'],
            'video_cards':  dstats['video_cards'],
            'transcribed':  dstats['transcribed'],
            'pending_transcription': dstats['pending_transcription'],
            'last_process': dstats['last_run'],
        },
        'monitored_accounts': sum(1 for a in accs.get('accounts', []) if a.get('enabled', True)),
        'total_accounts':   len(accs.get('accounts', [])),
        'scheduler':        'running (monitor@02:17 / process@02:47 / douyin@03:00)',
    }


@app.get('/health', tags=['System'], summary='健康检查（Docker 使用）')
def health():
    return {'ok': True}
