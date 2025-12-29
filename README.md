# 🎮 Epic Kiosk - 自动驾驶领取系统

![Docker](https://img.shields.io/badge/Docker-Enabled-blue?logo=docker)
![Python](https://img.shields.io/badge/Python-3.11-yellow?logo=python)
![Status](https://img.shields.io/badge/Status-Stable-green)

**Epic Kiosk** 是一个基于 Docker 的全自动化 Epic Games 免费游戏领取工具。它拥有现代化的 Web 管理界面，支持多账号托管，具备智能调度、自动防封和极简部署的特点。

<p align="center">
  <img src="assets/image_1.png" alt="Epic Kiosk Dashboard" width="800">
</p>

## ✨ 核心功能

* **🚀 自动驾驶**：一键启动，自动完成登录、两步验证（如需）及游戏领取流程。
* **📊 资产清单**：直观展示已领取的游戏库，支持点击海报直接跳转 Epic 商店查看详情。
* **🚦 错峰调度 (Jitter)**：内置智能随机延迟机制，避免多账号同时请求导致的风控封禁。
* **🛡️ 安全防滥用**：内置 IP 频率限制（Anti-Abuse），防止恶意频繁提交请求。
* **🧹 自动瘦身**：任务完成后自动清理浏览器缓存，单账号占用空间仅需 ~5MB。
* **☢️ 账号销毁**：支持“核弹级”账号移除，彻底粉碎本地数据和数据库记录，不留痕迹。
* **🐳 Docker 部署**：环境隔离，一键运行，支持 x86/ARM 架构（群晖、飞牛 NAS、Linux 服务器）。

## 1. 部署指南

## 前置要求
* 已安装 Docker 和 Docker Compose。

### 1. 克隆代码或下载
切换到本分支代码：
```bash
git clone -b Epic-Autopilot [https://github.com/10000ge10000/epic-awesome-gamer.git](https://github.com/10000ge10000/epic-awesome-gamer.git)
cd epic-awesome-gamer

```

### 2. 启动服务

直接运行 Docker Compose：

```bash
docker compose up -d

```

### 3. 访问控制台

打开浏览器访问：`http://服务器IP:18000`

---

## 📖 使用说明

1. **添加账号**：在首页控制台输入 Epic 邮箱和密码，点击 **"启动引擎"**。
* 系统会自动将任务加入队列。
* 如果是首次登录，系统会自动处理 Cookies。


2. **查看状态**：
* 页面下方会实时显示 Worker 的运行日志。
* 领取成功后，游戏会自动出现在“资产清单”中。


3. **资产管理**：
* 点击“资产清单”Tab 查看历史领取记录。
* 点击游戏封面可跳转至 Epic Store 对应页面。


4. **停止/删除**：
* 输入账号密码后点击红色删除按钮，系统将执行双重清理（数据库+物理文件），确保数据彻底移除。



---

## ⚙️ 目录结构

```text
epic-kiosk/
├── app/                # 核心代码
│   ├── main.py         # FastAPI 后端 (调度、API、防滥用)
│   ├── worker.py       # 业务逻辑 (浏览器自动化、清理)
│   └── deploy.py       # 底层领取脚本
├── templates/          # 前端页面
│   └── index.html      # 单页应用 UI
├── data/               # 持久化数据 (映射到宿主机)
│   ├── images/         # 游戏海报缓存
│   ├── user_data/      # 用户 Cookies 和配置
│   └── kiosk.db        # SQLite 数据库
├── docker-compose.yml  # 容器编排配置
└── Dockerfile          # 镜像构建文件

```

## ⚠️ 免责声明

本项目仅供学习和技术研究使用。开发者不对因使用本项目导致的账号封禁、数据丢失或其他损失承担任何责任。请合理使用，遵守 Epic Games 服务条款。

---

*Created by [10000*](https://github.com/10000ge10000)
