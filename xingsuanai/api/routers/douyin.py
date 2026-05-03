"""
/douyin 路由 — 抖音视频卡片处理、转录管理，以及抖小云博主关注列表管理。

- /douyin/stats            — 素材库统计
- /douyin/process          — 重新解析 NFO 生成视频卡片
- /douyin/transcribe       — 触发 Whisper 转录
- /douyin/dysync/*         — 抖小云 (port 10101) 博主管理
"""

from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from xingsuanai.core import douyin
from xingsuanai.core import dysync
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


# ── 抖小云博主管理 ────────────────────────────────────────────────────────────

class AddBloggerRequest(BaseModel):
    uperName:  str
    uperId:    str
    secUid:    str
    savePath:  str = ''
    openSync:  bool = False
    fullSync:  bool = False
    mySelfId:  str = ''


class ToggleSyncRequest(BaseModel):
    openSync:  bool
    fullSync:  bool = False
    savePath:  str = ''
    uperId:    str = ''


@router.get('/dysync/status', summary='检查抖小云连通性')
def dysync_status():
    """检查抖小云服务是否可达，并返回已登录的抖音账号。"""
    return dysync.check_connection()


@router.get('/dysync/stats', summary='抖小云视频库统计（视频数/博主数/磁盘占用）')
def dysync_stats():
    """从抖小云获取下载统计（视频总数、作者数、磁盘用量）。"""
    try:
        return dysync.get_stats()
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get('/dysync/bloggers', summary='查看抖小云关注博主列表')
def list_bloggers(
    page:      int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=200),
    name:      str = Query(None, description='按博主名过滤'),
    my_self_id: str = Query('', description='抖音账号 ID，留空取第一个'),
):
    """
    从抖小云读取博主关注列表。
    openSync=True 表示该博主的新视频会自动下载。
    fullSync=True 表示同时下载历史视频。
    """
    try:
        return dysync.get_bloggers(
            page=page, page_size=page_size,
            my_self_id=my_self_id, name_filter=name or None,
        )
    except Exception as e:
        raise HTTPException(502, str(e))


@router.patch('/dysync/bloggers/{record_id}/sync', summary='开关某博主的自动下载')
def toggle_blogger(record_id: str, req: ToggleSyncRequest):
    """
    开启或关闭指定博主的视频自动下载（openSync）。
    fullSync=True 则同时拉取历史视频（慎用，可能产生大量流量）。
    """
    try:
        return dysync.toggle_blogger_sync(
            record_id=record_id,
            open_sync=req.openSync,
            full_sync=req.fullSync,
            save_path=req.savePath,
            uper_id=req.uperId,
        )
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post('/dysync/bloggers', summary='手动添加博主到抖小云监控列表')
def add_blogger(req: AddBloggerRequest):
    """
    添加新博主（需要 uperId + secUid，从抖音主页 URL 获取）。
    抖音主页 URL 格式：https://www.douyin.com/user/{secUid}
    uperId 是数字 ID，需从抖音 App 或第三方工具获取。
    """
    try:
        return dysync.add_blogger(
            uper_name=req.uperName,
            uper_id=req.uperId,
            sec_uid=req.secUid,
            save_path=req.savePath,
            open_sync=req.openSync,
            full_sync=req.fullSync,
            my_self_id=req.mySelfId,
        )
    except Exception as e:
        raise HTTPException(502, str(e))


@router.delete('/dysync/bloggers/{record_id}', summary='从抖小云删除手动添加的博主')
def delete_blogger(record_id: str, my_self_id: str = Query(''), uper_id: str = Query('')):
    """仅能删除 isNoFollowed=True 的手动添加博主，抖音关注博主不可从此接口删除。"""
    try:
        return dysync.delete_blogger(record_id=record_id, my_self_id=my_self_id, uper_id=uper_id)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post('/dysync/sync', summary='立即触发抖小云全量同步')
def trigger_dysync():
    """触发抖小云立即开始同步所有开启了 openSync 博主的新视频。"""
    try:
        return dysync.trigger_sync()
    except Exception as e:
        raise HTTPException(502, str(e))
