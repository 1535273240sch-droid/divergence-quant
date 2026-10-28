"""PyInstaller 桌面端单文件 / 目录打包脚本。

用法:
    py build_desktop_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def build():
    root = Path(__file__).resolve().parent
    os.chdir(root)

    print(">>> 正在准备 PyInstaller 打包配置...")

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=DivergenceQuant",
        "-y",
        "--noconsole",
        "--clean",
        "--windowed",
        # 源码路径与依赖收集
        "--paths=src",
        "--collect-all=dquant",
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
        dist_dir = root / "dist" / "DivergenceQuant"
        print(">>> 正在复制资源与配置到发布目录:", dist_dir)
        import shutil
        # 复制 web 静态资源
        web_src = root / "web"
        web_dst = dist_dir / "web"
        if web_src.is_dir():
            shutil.copytree(web_src, web_dst, dirs_exist_ok=True)

        # 复制 src 源码包
        src_dir = root / "src"
        src_dst = dist_dir / "src"
        if src_dir.is_dir():
            shutil.copytree(src_dir, src_dst, dirs_exist_ok=True)

        # 复制 config
        cfg_src = root / "config"
        cfg_dst = dist_dir / "config"
        if cfg_src.is_dir():
            shutil.copytree(cfg_src, cfg_dst, dirs_exist_ok=True)

        # 复制 data 模板/数据库
        data_src = root / "data"
        data_dst = dist_dir / "data"
        data_dst.mkdir(parents=True, exist_ok=True)
        if data_src.is_dir():
            for f in data_src.glob("*"):
                if f.is_file():
                    shutil.copy2(f, data_dst / f.name)

        # 复制辅助启动脚本与文档
        for fname in ["app_icon.ico", "启动量化终端(无黑框).vbs", "桌面端使用指南.md", "start_desktop.bat"]:
            src_f = root / fname
            if src_f.is_file():
                shutil.copy2(src_f, dist_dir / fname)

        # 自动生成绿色压缩包
        zip_path = root / "dist" / "DivergenceQuant-Windows-x64"
        print(f">>> 正在打包绿色免安装分发压缩包: {zip_path}.zip ...")
        shutil.make_archive(str(zip_path), "zip", root_dir=dist_dir.parent, base_dir="DivergenceQuant")

        print("\n[成功] 打包完成！")
        print(f"独立程序目录: {dist_dir}")
        print(f"分发压缩包: {zip_path}.zip")
    else:
        print(f"\n[失败] 打包命令退出码: {ret.returncode}")
    return ret.returncode


if __name__ == "__main__":
    sys.exit(build())
