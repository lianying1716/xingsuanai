"""
/douyin 路由 — 抖音视频卡片处理与转录管理。
NFO 解析直接在容器内完成；Whisper 转录通过宿主机 .venv subprocess 调用。
"""

from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter

from xingsuanai.core import douyin
from xingsuanai.api.routers.pipeline import _jobs, _MAX_JOBS, _run_job

router = APIRouter()


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
    if len(_jobs) > _MAX_JOBS:
        oldest = sorted(_jobs, key=lambda k: _jobs[k]['created_at'])
        for old in oldest[:len(_jobs) - _MAX_JOBS]:
            _jobs.pop(old, None)
    return job_id


@router.get('/stats', summary='抖音素材库统计')
def get_stats():
    return douyin.get_stats()


@router.post('/process', summary='重新解析 NFO 并生成视频卡片（不含转录）')
def process_nfo():
    """
    扫描抖音案例库，解析 NFO，读取已有转录缓存，生成/更新视频卡片。
    不运行 Whisper，速度快（通常 < 30s）。
    """
    job_id = _register_job('douyin_process')
    t = threading.Thread(
        target=_run_job,
        args=(job_id, douyin.run, {}),
        daemon=True,
    )
    t.start()
    return {'job_id': job_id, 'status': 'started', 'note': 'NFO 解析，不含 Whisper 转录'}


@router.post('/transcribe', summary='触发 Whisper 转录（调用宿主机 .venv，耗时较长）')
def transcribe():
    """
    对尚未转录的 MP4 文件运行 faster-whisper（large-v3）。
    使用宿主机已有的 .venv，不在容器内安装模型。
    每个视频约 1-5 分钟，请通过 /pipeline/jobs/{job_id} 轮询进度。
    """
    job_id = _register_job('douyin_transcribe')
    t = threading.Thread(
        target=_run_job,
        args=(job_id, douyin.run_transcription, {}),
        daemon=True,
    )
    t.start()
    return {
        'job_id':  job_id,
        'status':  'started',
        'warning': 'Whisper large-v3 转录较慢，请耐心等待并通过 job_id 轮询进度',
    }
