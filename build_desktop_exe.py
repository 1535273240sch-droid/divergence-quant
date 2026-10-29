"""构建 DivergenceQuant Windows 桌面版（PyInstaller onedir）。

流程：
1. PyInstaller 生成 dist/DivergenceQuant（自包含 Python/Qt/FastAPI/前端）。
2. 复制配置、前端、图标、启动脚本。
3. 使用项目自身的建表逻辑预生成 data/engine.db、users.db、factors.db。
4. 运行打包后的 EXE 做无界面自检（健康接口 + 首页）。
5. 输出带版本号的便携 ZIP 与 SHA256。

用法：
    python build_desktop_exe.py --version 1.1.0
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "DivergenceQuant"
DEFAULT_VERSION = "1.1.0"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    printable = " ".join(f'"{part}"' if " " in part else part for part in cmd)
    print(f">>> {printable}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts: list[int] = []
    for chunk in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
        if len(parts) == 4:
            break
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def _write_version_file(path: Path, version: str) -> None:
    major, minor, patch, build = _version_tuple(version)
    path.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'DivergenceQuant'),
         StringStruct('FileDescription', 'DivergenceQuant Desktop'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'DivergenceQuant'),
         StringStruct('OriginalFilename', 'DivergenceQuant.exe'),
         StringStruct('ProductName', 'DivergenceQuant'),
         StringStruct('ProductVersion', '{version}'),
         StringStruct('LegalCopyright', 'DivergenceQuant')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def _pyinstaller_args(root: Path, version_file: Path) -> list[str]:
    hidden = [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "starlette.staticfiles",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "aiosqlite",
        "websockets",
        "websockets.legacy",
        "websockets.legacy.server",
        "dquant.strategies.s1_trend_pullback",
        "dquant.strategies.s2_oscillation_reversal",
        "dquant.strategies.s3_counter_trend_divergence",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
    ]
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=DivergenceQuant",
        f"--distpath={root / 'dist'}",
        f"--workpath={root / 'build' / 'pyinstaller'}",
        f"--specpath={root / 'build'}",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--paths=src",
        "--collect-all=dquant",
        "--collect-all=websockets",
        f"--add-data={root / 'web' / 'ice-console'}{os.pathsep}web/ice-console",
        f"--add-data={root / 'config'}{os.pathsep}config",
        f"--add-data={root / 'assets'}{os.pathsep}assets",
        f"--icon={root / 'assets' / 'app_icon.ico'}",
        f"--version-file={version_file}",
    ]
    for module in hidden:
        args.append(f"--hidden-import={module}")
    for module in ("pytest", "ruff", "mypy", "tkinter"):
        args.append(f"--exclude-module={module}")
    args.append("desktop_app.py")
    return args


def _copy_runtime_assets(root: Path, dist_dir: Path) -> None:
    shutil.copytree(root / "config", dist_dir / "config", dirs_exist_ok=True)
    shutil.copytree(
        root / "web" / "ice-console",
        dist_dir / "web" / "ice-console",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("preview", "__pycache__"),
    )
    icon = root / "assets" / "app_icon.ico"
    if icon.is_file():
        shutil.copy2(icon, dist_dir / "app_icon.ico")

    launcher = dist_dir / "启动DivergenceQuant.bat"
    launcher.write_text(
        "@echo off\r\n"
        "chcp 65001 >NUL\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0DivergenceQuant.exe\"\r\n",
        encoding="utf-8",
    )

    guide = root / "桌面端使用指南.md"
    if guide.is_file():
        shutil.copy2(guide, dist_dir / "使用说明.md")


def _init_databases(root: Path, dist_dir: Path) -> None:
    data_dir = dist_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            str(root / "scripts" / "init_desktop_data.py"),
            "--data-dir",
            str(data_dir),
        ],
        cwd=root,
    )


def _run_smoke(dist_dir: Path) -> None:
    exe = dist_dir / "DivergenceQuant.exe"
    if not exe.is_file():
        raise SystemExit(f"[失败] 未找到可执行文件: {exe}")
    try:
        subprocess.run([str(exe), "--headless-smoke", "--port", "0"], check=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        log_path = dist_dir / "desktop_debug.log"
        if log_path.is_file():
            print("--- desktop_debug.log (tail) ---")
            print("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        raise SystemExit(f"[失败] 打包产物自检失败，退出码: {exc.returncode}") from exc


def _clean_generated_state(dist_dir: Path) -> None:
    data_dir = dist_dir / "data"
    for path in (
        dist_dir / ".jwt_secret",
        dist_dir / "desktop_debug.log",
        data_dir / ".jwt_secret",
        data_dir / "desktop_debug.log",
    ):
        if path.exists():
            path.unlink()
    for pattern in ("*.db-wal", "*.db-shm", "*.log", "*.enc"):
        for leftover in data_dir.glob(pattern):
            leftover.unlink(missing_ok=True)


def _verify_databases(dist_dir: Path) -> None:
    data_dir = dist_dir / "data"
    expected = ("engine.db", "users.db", "factors.db")
    missing = [name for name in expected if not (data_dir / name).is_file()]
    if missing:
        raise SystemExit(f"[失败] 发布包缺少数据库文件: {', '.join(missing)}")
    for name in expected:
        size = (data_dir / name).stat().st_size
        print(f">>> 数据库已打包: data/{name} ({size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 DivergenceQuant Windows 桌面版")
    parser.add_argument("--version", default=os.getenv("DQUANT_DESKTOP_VERSION", DEFAULT_VERSION))
    args = parser.parse_args()
    version = args.version.strip().lstrip("v") or DEFAULT_VERSION

    root = Path(__file__).resolve().parent
    os.chdir(root)
    os.environ.setdefault("PYTHONUTF8", "1")

    icon = root / "assets" / "app_icon.ico"
    if not icon.is_file():
        raise SystemExit(f"[失败] 缺少应用图标: {icon}")

    for stale in (root / "build" / "pyinstaller", root / "dist" / APP_NAME):
        shutil.rmtree(stale, ignore_errors=True)

    build_dir = root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    version_file = build_dir / "version_info.txt"
    _write_version_file(version_file, version)

    print(f">>> 开始构建 DivergenceQuant 桌面版 v{version}")
    _run(_pyinstaller_args(root, version_file), cwd=root)

    dist_dir = root / "dist" / APP_NAME
    print(">>> 复制运行资源...")
    _copy_runtime_assets(root, dist_dir)

    print(">>> 预生成 SQLite 数据库...")
    _init_databases(root, dist_dir)

    print(">>> 运行打包产物自检...")
    _run_smoke(dist_dir)
    _clean_generated_state(dist_dir)
    _verify_databases(dist_dir)

    zip_base = root / "dist" / f"{APP_NAME}-{version}-Windows-x64"
    zip_path = zip_base.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    print(f">>> 生成便携压缩包: {zip_path}")
    shutil.make_archive(str(zip_base), "zip", root_dir=dist_dir.parent, base_dir=APP_NAME)

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    checksum_file = root / "dist" / "SHA256SUMS.txt"
    checksum_file.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")

    print("\n[成功] 桌面版构建完成")
    print(f"程序目录: {dist_dir}")
    print(f"便携压缩包: {zip_path}")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
