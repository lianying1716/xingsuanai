"""
/pipeline 路由 — 触发监控/加工任务，查询任务状态。
任务在后台线程中运行，通过 job_id 轮询进度。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from xingsuanai.core import monitor, processor

router = APIRouter()

# 简单内存 job 注册表（单实例服务，不需要持久化）
_jobs: dict[str, dict] = {}
_MAX_JOBS = 50  # 最多保留最近 N 条记录


def _register_job(job_type: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        'id':         job_id,
        'type':       job_type,
        'status':     'queued',
        'created_at': time.time(),
        'log':        [],
        'result':     None,
        'error':      None,
    }
    # 超过上限时丢弃最旧的
    if len(_jobs) > _MAX_JOBS:
        oldest = sorted(_jobs, key=lambda k: _jobs[k]['created_at'])
        for old in oldest[:len(_jobs) - _MAX_JOBS]:
            _jobs.pop(old, None)
    return job_id


def _run_job(job_id: str, fn, kwargs: dict) -> None:
    job = _jobs[job_id]
    job['status']     = 'running'
    job['started_at'] = time.time()
    logs: list[str]   = []

    def cb(msg: str) -> None:
        logs.append(msg)
        job['log'] = logs[-100:]  # 最多保留最近 100 条日志

    try:
        result = fn(progress_cb=cb, **kwargs)
        job.update({'status': 'done', 'result': result, 'log': logs,
                    'finished_at': time.time()})
    except Exception as e:
        job.update({'status': 'error', 'error': str(e), 'log': logs,
                    'finished_at': time.time()})


# ── 路由 ──────────────────────────────────────────────────────────────────────

class MonitorRequest(BaseModel):
    dry_run: bool = False


class ProcessRequest(BaseModel):
    rebuild: bool = False
    single_file: str | None = None


@router.post('/monitor', summary='触发公众号监控抓取')
def start_monitor(req: MonitorRequest = MonitorRequest()):
    """启动一次监控扫描，返回 job_id 用于轮询进度。"""
    job_id = _register_job('monitor')
    t = threading.Thread(
        target=_run_job,
        args=(job_id, monitor.run, {'dry_run': req.dry_run}),
        daemon=True,
    )
    t.start()
    return {'job_id': job_id, 'status': 'started', 'dry_run': req.dry_run}


@router.post('/process', summary='触发文章加工')
def start_process(req: ProcessRequest = ProcessRequest()):
    """启动一次文章加工，返回 job_id 用于轮询进度。"""
    job_id = _register_job('process')
    t = threading.Thread(
        target=_run_job,
        args=(job_id, processor.run,
              {'rebuild': req.rebuild, 'single_file': req.single_file}),
        daemon=True,
    )
    t.start()
    return {'job_id': job_id, 'status': 'started', 'rebuild': req.rebuild}


@router.get('/jobs', summary='查看最近任务列表')
def list_jobs(limit: int = 10):
    jobs = sorted(_jobs.values(), key=lambda j: j['created_at'], reverse=True)
    return {'jobs': jobs[:limit]}


@router.get('/jobs/{job_id}', summary='查看单个任务状态')
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f'Job {job_id} not found')
    return job
