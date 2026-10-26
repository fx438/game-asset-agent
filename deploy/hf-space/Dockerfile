# Hugging Face Spaces 在线演示镜像：构建时从 GitHub 拉取代码，Space 仓库里只需要这个文件和 README.md。
# 演示模式写死在这里：不读取任何 Key，不连 PostgreSQL / Redis / MinIO，重启后数据清空。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

ARG REPO=https://github.com/fx438/game-asset-agent.git
ARG REF=main
RUN git clone --depth 1 --branch ${REF} ${REPO} /app
WORKDIR /app
RUN pip install -r requirements.txt

ENV DEMO_MODE=true \
    DASHSCOPE_API_KEY= \
    AI_MOCK=true \
    DATABASE_URL=sqlite:////tmp/gaa/app.db \
    TASK_MODE=thread \
    STORAGE_BACKEND=local \
    LOCAL_STORAGE_DIR=/tmp/gaa/storage \
    IMAGE_SIZE=512*512

# Spaces 以 uid 1000 运行容器，数据放在可写的 /tmp
USER 1000
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
