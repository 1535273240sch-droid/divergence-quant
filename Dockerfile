# DivergenceQuant 生产环境容器镜像
# 遵循多阶段构建与最小镜像原则，禁止在容器内以 root 身份运行核心进程

FROM python:3.11-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir .

FROM python:3.11-slim as runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 系统用户
RUN groupadd -r dquant && useradd -r -g dquant -d /app -s /sbin/nologin dquant

# 拷贝已编译好的 Python 依赖库
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 拷贝源码与配置文件
COPY src/ /app/src/
COPY alembic.ini /app/
COPY alembic/ /app/alembic/

RUN mkdir -p /app/logs /app/reports /app/config && \
    chown -R dquant:dquant /app

USER dquant

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

CMD ["python", "-m", "dquant.scheduler.engine"]
