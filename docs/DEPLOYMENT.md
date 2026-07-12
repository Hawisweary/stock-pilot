# 部署 Runbook

## 1. 必填环境变量

| 变量 | 说明 | 必须 | 示例 |
|------|------|------|------|
| `AFR_API_KEY` | 写操作鉴权密钥（POST/PUT/PATCH/DELETE 必须） | **是** | `openssl rand -hex 32` |
| `AFR_DB_PATH` | SQLite 数据库路径 | **是** | `/data/afr.db` |
| `AFR_ENV` | 运行环境，生产必须设为 `production` | **是** | `production` |
| `AFR_CORS_ORIGINS` | 允许的前端域名，生产禁用 `*` | **是（生产）** | `https://your-domain.com` |
| `AFR_API_KEY_REQUIRED` | 是否对 GET 请求也要求 Key（与 `AFR_AUTH_ALL` 等效） | 否（默认 false） | `true` |
| `AFR_AUTH_ALL` | 同 `AFR_API_KEY_REQUIRED`，任一为 true 即启用全路径鉴权 | 否（默认 false） | `true` |
| `AFR_LOG_LEVEL` | 日志级别 | 否（默认 INFO） | `WARNING` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | AI 分析功能（可选） | 否 | — |

> **安全红线**：生产环境不可使用 `AFR_CORS_ORIGINS=*`，启动时会打印 warning。  
> 写操作永远需要 `AFR_API_KEY`，与 `AFR_API_KEY_REQUIRED` 无关。

---

## 2. 首次部署

```bash
# 1. 克隆并进入目录
git clone <repo-url> && cd ai-fundamental-researcher

# 2. 设置环境变量（建议写入 .env 文件后 source）
export AFR_API_KEY="$(openssl rand -hex 32)"
export AFR_DB_PATH="/data/afr.db"
export AFR_ENV=production
export AFR_CORS_ORIGINS="https://your-domain.com"

# 3. 安装后端依赖
cd backend && pip install -r requirements.txt && cd ..

# 4. 安装前端依赖并构建
cd frontend && npm install && npm run build && cd ..

# 5. 启动（后台保活）
./launch.sh daemon
```

---

## 3. 升级流程

```bash
# 1. 拉取新代码
git pull

# 2. 停止服务
./launch.sh stop

# 3. 安装新依赖（如有变动）
cd backend && pip install -r requirements.txt && cd ..
cd frontend && npm install && cd ..

# 4. 重建前端（如前端有变动）
cd frontend && npm run build && cd ..

# 5. 重启
./launch.sh daemon
```

数据库 migration 在后端启动时自动执行（`migrations.py`），无需手动操作。

---

## 4. 健康检查

```bash
# 后端 API
curl http://localhost:8800/api/health

# 预期响应
{"status":"ok","version":"..."}
```

`./launch.sh status` 会同时显示前端/后端/保活守护进程的状态。

---

## 5. WAL Checkpoint（定期维护）

SQLite 以 WAL 模式运行，建议每日低峰期执行 checkpoint：

```bash
sqlite3 /data/afr.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

可加入 cron：

```cron
0 3 * * * sqlite3 /data/afr.db "PRAGMA wal_checkpoint(TRUNCATE);" >> /var/log/afr-wal.log 2>&1
```

---

## 6. 备份

```bash
# 热备份（不需要停服）
sqlite3 /data/afr.db ".backup /backup/afr-$(date +%Y%m%d).db"
```

---

## 7. 日志位置

| 日志 | 路径 |
|------|------|
| 后端运行日志 | `.pids/backend.log` |
| 前端运行日志 | `.pids/frontend.log` |
| 保活守护日志 | `.pids/daemon.log` |
| 后端结构化日志 | stdout（由 `services/logger.py` 输出） |

---

## 8. 常见问题

**后端启动报 `AFR_API_KEY 未设置`**  
→ 设置 `AFR_API_KEY` 环境变量后重启。当前状态允许启动，但所有写操作无鉴权（仅本地开发可接受）。

**CORS 被浏览器拒绝**  
→ 检查 `AFR_CORS_ORIGINS` 是否包含前端实际域名（含协议和端口）。

**`Error: no such table: stock_score_profiles`**  
→ 运行 migration（通常重启后端即可）：`python -c "import migrations, sqlite3; migrations.run_migrations(sqlite3.connect('/data/afr.db'))"`

**卖出报「涨停价附近无法买入」**  
→ 当日行情数据与实时价源冲突，属正常涨停保护逻辑。次日交易日可正常操作。
