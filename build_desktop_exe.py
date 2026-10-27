"""PyInstaller 桌面端单文件 / 目录打包脚本。

用法:
    py build_desktop_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def build():
    root = Path(__file__).resolve().parent
    os.chdir(root)

    print(">>> 正在准备 PyInstaller 打包配置...")

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=DivergenceQuant",
        "--noconsole",
        "--clean",
        "--windowed",
        # 源码路径
        "--paths=src",
        # 资源文件打包
        "--add-data=web/ice-console;web/ice-console",
        "--add-data=config;config",
        # 隐式依赖
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=fastapi",
        "--hidden-import=starlette",
        "--hidden-import=starlette.staticfiles",
        "--hidden-import=sqlalchemy.dialects.sqlite",
        "--hidden-import=dquant.strategies.s1_trend_pullback",
        "--hidden-import=dquant.strategies.s2_oscillation_reversal",
        "--hidden-import=dquant.strategies.s3_counter_trend_divergence",
        "--hidden-import=PySide6.QtWebEngineWidgets",
        "--hidden-import=PySide6.QtWebEngineCore",
        "desktop_app.py",
    ]

    print(">>> 开始执行打包命令:")
    print(" ".join(args))
    ret = subprocess.run(args)
    if ret.returncode == 0:
        print("\n[成功] 打包完成！生成文件位于 dist/DivergenceQuant 目录下。")
    else:
        print(f"\n[失败] 打包命令退出码: {ret.returncode}")
    return ret.returncode


if __name__ == "__main__":
    sys.exit(build())
