# 📖 中考古诗词背诵助手

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Release](https://img.shields.io/github/v/release/Alewn666/recitation-helper?label=release)
![Docker](https://img.shields.io/badge/Docker-%E4%B8%80%E9%94%AE%E9%83%A8%E7%BD%B2-2496ED?logo=docker&logoColor=white)

> 一个班共用、不用注册的背诵练习网页：把古诗词原文丢进去，自动出「上句填下句 / 整句默写 / 作者朝代 / 字词释义」的卡片，判分会忽略空格标点并指出具体错字，再按简化的间隔重复安排复习。

它是按真实课堂场景做的：全班用同一台机器（NAS 或教室服务器），学生不需要账号、不需要记密码，只用匿名编号认领自己的进度——既省去账号管理，也不收集学生姓名。老师维护的只是一份 `data/deck.json` 题库。

## 出题不是手录题库

大多数背诵软件要人工录几百道题；这里是从**原文自动生成卡片**：

| 题型 | 生成方式 |
| --- | --- |
| 上句填下句 | 按句读切分，取相邻两句 |
| 整句默写 | 给出释义/上下文，默写全句 |
| 作者朝代 | 从篇目元数据生成 |
| 字词释义 | 针对重点字词提问 |

每次随机决定是**选择题**还是**填空题**，同一篇目反复练也不会总撞同一张卡。

## 判分与记忆

- **宽容判分**：自动忽略空白与标点差异，不会因为多打一个空格判错；但会**指出具体错字**，不是笼统给个 ✗。
- **错题本**（`/api/wrong`）：答错的字句单独收拢，方便集中重练。
- **简化间隔重复**（`/api/progress`）：按掌握情况调整复现节奏，优先推不熟的。
- **统计**（`/api/stats`）：按匿名编号看正确率与进度，谁卡在哪一句一目了然。

## 快速开始

```bash
sh deploy.sh                 # 一键部署到 NAS（同步代码 → 建镜像 → 重建容器 → 健康检查）
```

或者在任意有 Docker 的机器上：

```bash
docker build -t recitation-helper .
docker run -d --name recite --restart unless-stopped \
  -p 8090:8090 -e TZ=Asia/Shanghai \
  -e RECITE_DATA_DIR=/data -v "$PWD/data:/data" recitation-helper
```

本地开发：

```bash
pip install -r requirements.txt
python3 app.py               # 默认 8090
```

服务起来后 `curl http://127.0.0.1:8090/api/health` 应返回健康状态。

> ⚠️ **部署时题库要跟着走**：容器挂的是 `data/ → /data`，`deploy.sh` 专门拷了 `data/deck.json`。漏掉这一步，页面能开但出题会 500。题库原文属于运行时数据，未纳入公开仓库，请按 `app.py` 里的结构自备一份 `data/deck.json`。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8090` | 监听端口 |
| `RECITE_DATA_DIR` | `./data` | 题库与进度数据目录 |
| `RECITE_SECRET` | — | 设置页登录用的密钥（`/api/settings/login`） |

## 目录结构

```
recitation-helper/
├── app.py                  # Flask 服务（约 450 行）
├── data/                   # 题库与进度目录（需自行准备 deck.json）
├── deploy.sh               # NAS 一键部署，含题库同步与健康检查
├── Dockerfile              # python:3.11-slim + gunicorn（8 线程）
├── templates/ static/      # 单页练习界面
├── test_engine.py          # 出题与判分引擎测试
├── ui_test.py              # 界面回归
├── verify_live.py          # 活体接口校验
└── docs/检查与修复记录.md   # 排障实录
```

## 测试

```bash
python3 test_engine.py      # 卡片生成、判分容错、错字定位
python3 ui_test.py          # 界面回归
python3 verify_live.py      # 对运行中服务做接口校验
```

## 架构一览

```
浏览器（单页）  ──  /api/cards /api/answer /api/wrong /api/stats  ──  Flask
                                                                   │
                                          data/deck.json（题库）＋ 进度（匿名编号）
```

后端只有一个 `app.py`，无数据库、无外部服务，`gunicorn` 单进程 8 线程即可带一个班。前端是一张卡片式单页，手机和电脑浏览器都能直接练。

## 说明与边界

- 进度按匿名编号区分，编号本身不做身份认证，适合可信的班级局域网环境。
- 间隔重复是简化实现（按熟练度分档），不是完整的 SM-2 / FSRS 算法。
- `data/`、`.env`、`*.db`、`*.log` 已在 `.gitignore` 中排除，公开仓库不含真实数据；文档中的 IP 与口令均为示例值。

## 许可证

MIT — 见 [LICENSE](LICENSE)。本项目由 NAS 归档包整理后公开发布。
