"""
内置调度器 — 替代 NAS 上的 cron 条目。
monitor 每天 02:17，process 每天 02:47。
"""

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def start(monitor_fn: Callable, process_fn: Callable, douyin_fn: Callable | None = None) -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    _scheduler.add_job(monitor_fn, CronTrigger(hour=2, minute=17), id='monitor', replace_existing=True)
    _scheduler.add_job(process_fn, CronTrigger(hour=2, minute=47), id='process', replace_existing=True)
    if douyin_fn:
        _scheduler.add_job(douyin_fn, CronTrigger(hour=3, minute=0), id='douyin', replace_existing=True)
    _scheduler.start()
    logger.info('调度器已启动：monitor@02:17 / process@02:47 / douyin@03:00 (Asia/Shanghai)')


def stop() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info('调度器已停止')
