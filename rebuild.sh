#!/bin/bash
# 重建并重启 xingsuanai 容器
# 在 NAS 上执行：bash /vol1/1000/素材库/星算运营库/xingsuanai/rebuild.sh

set -e
cd /vol1/1000/素材库/星算运营库/xingsuanai

echo "=== 拉取最新代码（如有 git）==="
# git pull --ff-only 2>/dev/null || true

echo "=== 重建镜像 ==="
docker compose build --no-cache

echo "=== 重启容器 ==="
docker compose up -d

echo "=== 等待服务就绪 ==="
sleep 5
curl -s http://localhost:7800/health || echo "服务暂未就绪，请稍候"
