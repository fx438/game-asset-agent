---
title: Game Asset Agent
emoji: 🎮
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: 需求文档 → 风格母版 → 批量生图 → 审查 → 入库的多 Agent 流水线演示
---

# 游戏资源图片 Agent · 在线演示

源码：https://github.com/fx438/game-asset-agent

这是在线演示，不调用任何付费模型：示例需求展示的是预先画好的示例图，上传其他文档时由本地程序现画像素图。每个项目最多 32 张图，Space 重启后数据会清空。

## 部署说明（给仓库维护者）

1. 在 huggingface.co 新建 Space，SDK 选 **Docker**，模板选 Blank。
2. 把本目录的 `Dockerfile` 和 `README.md` 上传到 Space 仓库根目录（网页上 Files → Add file 即可）。
3. **不要在 Space 的 Settings → Variables and secrets 里填任何 Key**。镜像里已经写死 `DEMO_MODE=true`，即使填了也会被忽略。
4. GitHub 代码更新后，在 Space 的 Settings 里点 Factory rebuild，重新拉取最新代码。
