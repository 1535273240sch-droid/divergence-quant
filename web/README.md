# DivergenceQuant 前端（交接版）

这是 DivergenceQuant 量化交易系统的前端模块，独立打包出来，可直接开发。

## 一、这里面包含两套东西

### 1. React 正式前端（`src/`，M12 规划的正主）
- 技术栈：React + TypeScript + Vite + React Router + Vitest + ESLint
- **已完成**：黑金美学设计令牌系统（`src/styles/tokens.css`）、11 个 UI 组件（`src/ui/`）、响应式应用壳（桌面顶栏 + 手机底部标签栏）、全局快捷键与命令面板、路由框架
- **待做**：11 个页面里 10 个业务页是"建设中"占位（见 `src/app/AppRoutes.tsx`），只有 `/dev/tokens` 设计令牌页是真页面；所有页面需要对接后端 API

### 2. 高保真原型（`index.html` + `app.js` + `styles.css`）
- 无需安装任何依赖，浏览器直接打开 `index.html` 就能看
- 包含全部业务页的外观和交互（选币器/工作台K线/待确认队列/持仓/回测热力图等），**数据是写死的演示数据**——做正式页面时照着它的视觉与交互实现，但数据要从 API 拿

## 二、怎么跑起来（需要 Node.js 18+）

```bash
npm install        # 首次
npm run dev        # 开发服务器 (Vite)
npm test           # 跑 201 项前端测试
npm run build      # 构建产物在 dist/
```

## 三、后端对接指南

后端是 FastAPI（主项目仓库的 `src/dquant/api/`），对接方式：

1. **接口契约（最重要的文档）**：后端启动后访问 `http://127.0.0.1:8000/docs`，有全部接口的 OpenAPI 交互文档（可以在线试调用看返回结构）。启动命令（在后端项目根目录）：`python -m uvicorn dquant.api.main:app --port 8000`
2. **鉴权**：所有业务接口需要 `Authorization: Bearer <JWT>`，先调 `POST /api/v1/auth/login`（用户名 + 密码 + TOTP 动态码）拿 token。登录/双因素/防爆破是真实实现
3. **实时推送**：WebSocket 端点 `/ws`，支持心跳（发 PING 回 PONG）
4. **主要接口**（前缀都是 `/api/v1`）：
   - `GET /market/candidates` 选币候选列表
   - `GET /market/positions` 持仓
   - `GET /signals/pending`、`POST /signals/{id}/confirm|reject` 待确认信号与确认/拒绝
   - `GET /strategies`、`PUT /strategies/{id}/params` 策略列表与参数
   - `POST /emergency/stop-open|close-all|resume` 紧急开关
   - `GET /health` 健康检查（无需鉴权）
5. **注意**：部分接口目前返回占位 mock 数据（后端正在切换为真实数据源），**字段结构以 `/docs` 的 schema 为准**——后端换数据源不改字段，前端按 schema 开发不受影响

## 四、建议的开发顺序

1. 总览页（`/`）→ 2. 选币器（`/screener`）→ 3. 持仓（`/positions`）→ 4. 待确认信号（`/confirm`）→ 5. 策略（`/strategies`）→ 6. 风控（`/risk`）→ 其余页面

前两个页面后端已具备真实数据（选币结果已落库），适合先做。

## 五、更多文档

- `docs/M12_前端模块交接文档.md` —— 模块原始交接文档（现状/约定/待办）
- `docs/接口契约.md` —— 全系统接口契约（各模块数据的生产方/消费方）

## 六、目录结构

```
├── src/                React 正式前端源码
│   ├── app/            应用壳、路由、状态、快捷键
│   ├── ui/             UI 组件库 (Button/Panel/Tag/...)
│   ├── styles/         设计令牌 (tokens.css/base.css)
│   ├── dev/            设计令牌展示页 (目前唯一真页面)
│   └── test/           测试配置
├── docs/               交接与契约文档
├── index.html          高保真原型 (浏览器直接打开)
├── app.js / styles.css 原型的逻辑与样式
└── package.json        依赖与脚本
```
