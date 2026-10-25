# M00 HANDOFF

## 状态

已完成 M00 的第一版工程基线与公共契约。

## 本次完成

1. 建立 `pyproject.toml`、`.gitignore`、`.gitattributes`、`.env.example` 和 `pre-commit-config.yaml`。
2. 建立 `src/dquant` 正式 Python 包。
3. 建立 M01～M13 的 Python 包边界，避免后续 AI 随意跨目录放置代码。
4. 建立 `src/dquant/contracts/`，实现公共枚举和 Pydantic 数据模型。
5. 建立 `config/config.yaml` 和 `config/config.backtest.yaml` 的无密钥模板。
6. 建立 M00 单元测试和项目结构测试。
7. 保留根目录 `risk_calculator.py`、`test_risk_calculator.py` 和 `web/` 静态原型，不在 M00 阶段破坏已有实现。

## 修改文件

- `README.md`
- `pyproject.toml`
- `.gitignore`
- `.gitattributes`
- `.env.example`
- `pre-commit-config.yaml`
- `config/config.yaml`
- `config/config.backtest.yaml`
- `src/dquant/**`
- `tests/unit/test_contracts.py`
- `tests/test_project_layout.py`
- `协作/00_总控/接口契约.md`
- `协作/00_总控/M00_HANDOFF.md`

## 公共接口

代码入口：

```python
from dquant.contracts import Candle, Signal, RiskDecision
```

契约模型的具体字段以 `src/dquant/contracts/models.py` 为准，枚举值以 `src/dquant/contracts/types.py` 为准。

## 验收结果

在项目根目录已执行：

```text
ruff check .                 # 通过
ruff format --check src tests # 通过，19 files already formatted
py -m pytest -q              # 通过，8 passed
python -m pytest -q           # 当前环境失败：python 命令不存在
mypy .                       # 当前环境失败：mypy 命令不存在
py -m mypy .                  # 当前环境失败：未安装 mypy 模块
PYTHONPATH=src py -c "from dquant.contracts import Candle, Signal, RiskDecision"  # 通过
```

当前环境有 Windows `py` 启动器，因此使用 `py -m pytest -q` 完成了实际测试验证。mypy 尚未安装，不能将类型检查报告为通过。

## 已知边界

- M00 不实现数据库连接、OKX API、策略、回测、订单执行、FastAPI 或 React 页面。
- M00 不安装依赖，不连接外部服务，不读取或生成真实密钥。
- 根目录 `risk_calculator.py` 尚未移动到 `src/dquant/risk/`，由 M08 在不破坏现有测试的前提下迁移。
- 现有 `web/` 是静态 HTML/CSS/JS 原型，由 M12 迁移为 React/Vite 应用。

## 下一步

先由 M01 实现配置加载、数据库连接边界和 Alembic 迁移；M02/M03/M08 可在公共契约不变的前提下准备各自测试。
