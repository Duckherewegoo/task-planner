# ═══════════════════════════════════════════════════════════════════
# 生产镜像 —— 多阶段构建
# 只装运行时，不含 tests / scripts / abandon
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: Builder ──
FROM python:3.12-slim AS builder

WORKDIR /build

# 先拷依赖清单，利用 Docker 层缓存
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 构建 wheel
RUN pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /wheels


# ── Stage 2: Runtime ──
FROM python:3.12-slim

# 系统依赖（cairosvg 需要 cairo / pango；CJK 字体给渲染用）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户
RUN useradd -m -u 1000 planner

WORKDIR /app

# 装 wheel（依赖 + 本体）
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

# 只拷运行时数据（外部配置 + 词库 + 提示词已在 wheel 里）
COPY --chown=planner:planner config/ ./config/
COPY --chown=planner:planner data/   ./data/

# 日志 / 导出目录
RUN mkdir -p /app/logs /app/exports \
    && chown -R planner:planner /app

USER planner

# 端口：主应用 8050，配置工具 8051
EXPOSE 8050 8051

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8050/').read(1)" || exit 1

# 默认启动主应用（可用 command 覆盖成 config tool）
ENTRYPOINT ["task-planner"]
