"""建立计划书第 7 章定义的初始表结构。"""

from alembic import op

from dquant.db import models  # noqa: F401 让所有模型注册到 Base.metadata
from dquant.db.base import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """使用已审核的 SQLAlchemy 元数据创建初始表。"""

    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    """按依赖安全顺序删除初始表。"""

    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=False)
