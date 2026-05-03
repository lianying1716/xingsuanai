FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（编译某些 Python 包需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY xingsuanai/ xingsuanai/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7800/health')" || exit 1

CMD ["uvicorn", "xingsuanai.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "7800", \
     "--workers", "1", \
     "--log-level", "info"]
