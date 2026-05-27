# Phase 0 验收问题总结

最后更新：2026-05-27

## 结论

本文件最初记录的是 2026-05-22 的 Phase 0 验收缺口。当时 `codex/phase-0` worktree 的本地核心链路基本可运行，但 public URL 尚未验证。

截至 2026-05-27，Phase 0 在仓库范围内已经收敛关闭：

- 本地 deployable skeleton 已验证并合入 `main`。
- Web console 的右侧已选 session 详情自动刷新缺口已修复，并有前端回归测试覆盖。
- VPS public demo 已在 2026-05-26 通过临时 raw-IP HTTP 验证：公网 web、API `/health`、API session/run polling、Redis/arq worker 到前端自动 `completed` 的闭环均已通过。
- 真实 VPS IP、SSH target 和 public host 不提交到仓库文档；这些值只保存在 VPS-local `.env` 或私有运维记录中。
- 域名 DNS 和 HTTPS 绑定属于外部部署运维，不再作为 Phase 0 仓库任务阻塞项。

本轮已处理的历史缺口：

- Web console 的右侧已选 session 详情自动刷新缺口已修复，并新增了前端回归测试覆盖该轮询路径。

另外需要注意：下面的验收细节保留为历史记录。当前判断应以 `main`、`README.md` 和 VPS deployment plan 的 2026-05-26/2026-05-27 更新为准。

## 已通过项

在 `.worktrees/codex-phase-0` 中执行的本地验证结果：

- `cd backend && .venv/bin/ruff check .` 通过。
- `cd backend && .venv/bin/pytest -q` 通过，结果为 `4 passed`。
- `cd apps/web && npm install` 成功，未发现漏洞。
- `cd apps/web && npm test` 覆盖已选 session detail 跟随轮询结果更新。
- `cd apps/web && npm run build` 通过，Vite production build 成功。
- `docker compose up --build -d` 成功启动 `api`、`worker`、`web`、`postgres`、`redis`。
- `curl http://localhost:8000/health` 返回 `{"status":"ok","service":"puppyrun-api"}`。
- 通过 API 创建 session、启动 dummy Agent run 后，worker 能把 session 更新为 `completed`，并写入 summary：`Phase 0 dummy Agent completed. Real research workflow is not enabled yet.`
- 浏览器验收通过：在 `http://localhost:5173` 创建 session 并启动 dummy Agent run 后，不点击手动 `Refresh`，右侧 `Run status` detail panel 自动更新为 `completed` 并显示 summary。

这些证据说明后端 API、数据库、Redis/arq worker、Docker Compose 和 dummy job 的最小闭环已经跑通。

## 历史未通过或未完全满足项

### 1. 缺少 public URL 验证

Phase 0 设计文档的 success criteria 包含：

- A public URL can load the app.

当前实现只有：

- `docker-compose.yml`
- `backend/Dockerfile`
- `apps/web/Dockerfile`
- 本地 URL：`http://localhost:5173` 和 `http://localhost:8000`

未看到 Netlify、Render、Fly.io、Railway、Cloud Run、Vercel 或其他公开部署配置，也没有可访问的 public URL 记录。

历史判断：本地 deployable skeleton 通过，但 public URL success criterion 尚未满足。

当前状态：该缺口已经通过 VPS 临时 raw-IP HTTP public demo 验证关闭。域名 DNS 和 HTTPS 可以继续作为部署运维优化处理，但不再阻塞 Phase 0 仓库收敛。

### 2. 前端已选 session 详情自动刷新缺口已处理

浏览器验证流程：

1. 打开 `http://localhost:5173`。
2. 创建 session。
3. 点击 `Start dummy Agent run`。
4. worker 完成任务后，左侧 session 列表显示该 session 为 `completed`。
5. 修复前：右侧 `Run status` 详情仍停留在 `queued`，没有自动显示 `completed` 和 summary。
6. 修复后：轮询会按当前选中 session id 同步右侧详情；新增 `apps/web/src/App.test.tsx` 验证不点击手动 `Refresh` 时，detail panel 会从 `queued` 更新为 `completed` 并显示 summary。

该问题原本不完全满足 Phase 0 success criteria 中的：

- The frontend receives updated status.

更精确地说，前端 session list 已收到更新状态，但当前选中 session 的 detail panel 没有自动同步更新。

疑似原因在 `apps/web/src/App.tsx`：

- `refreshSessions()` 会根据 `selected` 更新当前详情。
- 但定时轮询注册在 `useEffect(..., [])` 中，闭包捕获的是初始 `selected`。
- 因此 interval 中的 `refreshSessions()` 可能长期看不到最新 selected state。

可选修复方向：

- 让 `refreshSessions` 接收当前 selected id，避免依赖过期闭包。
- 用 `useRef` 保存当前 selected id。
- 或把轮询 effect 的依赖和函数稳定性重新整理，确保轮询能更新当前 detail panel。

修复后需要重新用浏览器验证：不点击手动 Refresh，只启动 dummy Agent run，等待轮询自动把右侧状态更新为 `completed` 并显示 summary。

## 建议处理顺序

1. 已修复 Web console 已选 session 详情自动刷新问题。
2. 已重新运行本地验证命令：`ruff check .`、`pytest -q`、`npm test`、`npm run build`。
3. 已重新跑 Docker Compose 和浏览器验收，确认右侧 detail panel 自动更新。
4. 如果 Phase 0 必须严格满足 public URL 标准，再补一个最小公开部署计划和配置；否则需要在 Phase 0 文档里明确把 public URL 延后，并说明当前 Phase 0 只证明本地 deployable skeleton。

## 当前验收判断

- 如果验收对象是 `main`：Phase 0 仓库范围已关闭，本地核心链路、前端 detail 自动刷新、VPS public demo 闭环都已验证。
- `codex/phase-0` worktree 和本地分支已经是历史执行路径，不再作为当前验收对象。
- 域名 DNS、HTTPS 证书、真实 URL 记录方式属于外部部署运维边界；真实 public URL 不写入仓库 README。
