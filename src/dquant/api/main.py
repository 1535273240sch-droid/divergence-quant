"""uvicorn 部署入口。

docker-compose 的 api 服务使用 `uvicorn dquant.api.main:app`，
本模块将其桥接到 dquant.api.app 的应用单例。
"""

from dquant.api.app import app, create_app

__all__ = ["app", "create_app"]
