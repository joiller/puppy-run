# Phase 0 验收问题总结

最后更新：2026-05-22

## 结论

`codex/phase-0` worktree 的 Phase 0 本地核心链路基本可运行，但还不能判定为完整满足 Phase 0 验收标准。

剩余阻塞原因：

- 设计标准中的 public URL 尚未验证，当前只有本地 Docker Compose。

本轮已处理的缺口：

- Web console 的右侧已选 session 详情自动刷新缺口已修复，并新增了前端回归测试覆盖该轮询路径。

另外需要注意：根目录 `main` 分支仍主要是设计文档；Phase 0 实现位于 `.worktrees/codex-phase-0` / `codex/phase-0`。讨论 Phase 0 是否满足验收时，必须先说明验收对象是哪个分支或 worktree。

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

## 未通过或未完全满足项

### 1. 缺少 public URL 验证

Phase 0 设计文档的 success criteria 包含：

- A public URL can load the app.

当前实现只有：

- `docker-compose.yml`
- `backend/Dockerfile`
- `apps/web/Dockerfile`
- 本地 URL：`http://localhost:5173` 和 `http://localhost:8000`

未看到 Netlify、Render、Fly.io、Railway、Cloud Run、Vercel 或其他公开部署配置，也没有可访问的 public URL 记录。

判断：本地 deployable skeleton 通过，但 public URL success criterion 尚未满足。

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

- 如果验收对象是 `main`：不满足 Phase 0，因为实现尚未进入主分支。
- 如果验收对象是 `codex/phase-0`：本地核心链路通过，前端 detail 自动刷新缺口已处理，但 public URL 仍未验证。
