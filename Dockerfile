# 后端镜像：api 与 worker 共用（不同启动命令）。
# 用 3.12-slim —— 宿主机是 3.14，但 onnxruntime/xgboost/rapidocr 的 wheel 对 3.14 支持
# 尚不完整；3.12 有稳定 wheel。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# 运行时系统库：onnxruntime 需 libgomp1；opencv(cv2) 需 libgl1 + libglib2.0-0
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 装依赖：libpff-python(.pst/.ost 解析) 需从源码编译 → 临时装 build-essential/pkg-config，
# pip 装完后 purge 掉，避免把编译器留在最终镜像里。
# 先装 CPU 版 torch：某个传递依赖会拉 torch，默认在 aarch64 上会带上 ~3GB 的
# nvidia CUDA 库（nccl/cudnn/cublas，Mac 上完全用不到）。预装 CPU-only torch 满足
# 依赖，避免拉 CUDA wheel —— 镜像更小、构建更快（运行时并不真的用 torch）。
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config \
    && pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements.txt \
    && apt-get purge -y build-essential pkg-config && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000

# 默认跑 api；worker 在 compose 里用 command 覆盖为 python -m src.backend.worker
CMD ["uvicorn", "src.backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
