"""DivergenceQuant / CHRONO QUANT 原生桌面量化交易终端。

三合一架构:
1. 嵌入式 SQLite: data/ 目录零配置持久化 (engine.db, users.db, factors.db, .jwt_secret, okx-credentials.enc)
2. 内置 ASGI 服务: Uvicorn 后台服务线程, 同时托管 FastAPI 核心 API 与 web/ice-console 前端控制台
3. 原生桌面窗口: PySide6 (Chromium QWebEngineView), 支持纯黑/银白双模主题、系统托盘、量化引擎控制
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

# 禁用本地回环代理拦截，强制直连
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-proxy-server"

# 确定应用程序真实物理根目录 (处理 PyInstaller 打包环境)
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent

def log_debug(msg: str) -> None:
    try:
        with open(ROOT_DIR / "desktop_debug.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    except Exception:
        pass

log_debug(f"Process started. PID={os.getpid()}, frozen={getattr(sys, 'frozen', False)}, ROOT_DIR={ROOT_DIR}")

SRC_DIR = ROOT_DIR / "src"
if SRC_DIR.is_dir() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
os.chdir(ROOT_DIR)

# 确保本地数据目录与密钥初始化
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
JWT_SECRET_FILE = DATA_DIR / ".jwt_secret"
if not os.getenv("DQUANT_JWT_SECRET"):
    if JWT_SECRET_FILE.is_file():
        os.environ["DQUANT_JWT_SECRET"] = JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
    else:
        new_secret = secrets.token_hex(32)
        JWT_SECRET_FILE.write_text(new_secret, encoding="utf-8")
        os.environ["DQUANT_JWT_SECRET"] = new_secret

os.environ.setdefault("DQUANT_ENGINE_DB", str(DATA_DIR / "engine.db"))
os.environ.setdefault("DQUANT_USERS_DB", str(DATA_DIR / "users.db"))
os.environ.setdefault("DQUANT_FACTORS_DB", str(DATA_DIR / "factors.db"))
os.environ.setdefault("DQUANT_CREDENTIALS_FILE", str(DATA_DIR / "okx-credentials.enc"))

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkProxy
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


def find_free_port(preferred: int = 8818) -> int:
    """优先使用 preferred 端口，若被占用则自动探测可用端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def create_procedural_icon() -> QIcon:
    """绘制高精矢量感应用与托盘图标 (CQ 霓虹绿白折射风格)。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 纯黑液态底板
    p.setBrush(QBrush(QColor(12, 16, 22)))
    p.setPen(QPen(QColor(0, 245, 160), 2.5))
    p.drawRoundedRect(3, 3, 58, 58, 14, 14)

    # 金融 K 线抽象折线
    p.setPen(QPen(QColor(255, 255, 255), 2.5))
    p.drawLine(14, 44, 26, 26)
    p.drawLine(26, 26, 38, 36)
    p.setPen(QPen(QColor(0, 245, 160), 2.8))
    p.drawLine(38, 36, 50, 16)

    # 端点光晕
    p.setBrush(QBrush(QColor(0, 245, 160)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(47, 13, 6, 6)

    p.end()
    return QIcon(pixmap)


class UvicornServerThread(threading.Thread):
    """在后台专用线程运行 FastAPI ASGI 服务。"""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(daemon=True, name="UvicornServerThread")
        self.host = host
        self.port = port
        self.server = None
        self.error: Exception | None = None

    def run(self) -> None:
        import traceback
        log_debug(f"UvicornServerThread starting on {self.host}:{self.port}...")
        try:
            import uvicorn
            log_debug("Uvicorn imported.")
            from dquant.api.app import create_app
            log_debug("create_app imported.")

            app = create_app()
            log_debug("create_app() succeeded.")
            config = uvicorn.Config(
                app=app,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
            )
            self.server = uvicorn.Server(config)
            log_debug("uvicorn.Server.run() starting...")
            self.server.run()
            log_debug("uvicorn.Server.run() finished.")
        except Exception as e:
            self.error = e
            log_debug(f"UvicornServerThread EXCEPTION: {traceback.format_exc()}")

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True


class EngineWorkerThread(threading.Thread):
    """量化策略与行情引擎工作线程。"""

    def __init__(self, source: str = "synthetic", interval: float = 30.0) -> None:
        super().__init__(daemon=True, name="TradingEngineThread")
        self.source = source
        self.interval = interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self.is_running = False

    def run(self) -> None:
        from dquant.scheduler.engine import EngineOptions, TradingEngine

        options = EngineOptions(
            source=self.source,
            dry_run=True,
            db_path=str(DATA_DIR / "engine.db"),
            interval_sec=self.interval,
        )
        engine = TradingEngine(options)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.is_running = True
        self._task = self._loop.create_task(engine.run())
        try:
            self._loop.run_until_complete(self._task)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            self.is_running = False
            self._loop.close()

    def stop(self) -> None:
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)
        self.is_running = False


class MainWindow(QMainWindow):
    """量化交易桌面主窗口。"""

    def __init__(
        self,
        url: str,
        engine_worker: EngineWorkerThread | None,
        server_thread: UvicornServerThread,
    ) -> None:
        super().__init__()
        self.url = url
        self.engine_worker = engine_worker
        self.server_thread = server_thread
        self.devtools_window: QMainWindow | None = None

        self.setWindowTitle("CHRONO QUANT · 顶级量化交易终端")
        self.resize(1440, 900)
        self.center_window()
        self.app_icon = create_procedural_icon()
        self.setWindowIcon(self.app_icon)

        # 核心 QWebEngineView 视窗
        self.web_view = QWebEngineView(self)
        self.setup_web_settings()

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.web_view)

        container = QWidget()
        container.setLayout(layout)
        container.setStyleSheet("background-color: #000000;")
        self.setCentralWidget(container)

        # 快捷键支持
        self.setup_shortcuts()

        # 系统托盘
        self.setup_tray()

        # 加载完成检查与自动重试
        self._retry_count = 0
        self.web_view.loadFinished.connect(self.on_load_finished)

        # 加载本地前端页面
        self.web_view.load(QUrl(self.url))

    def on_load_finished(self, ok: bool) -> None:
        if not ok:
            if self._retry_count < 10:
                self._retry_count += 1
                QTimer.singleShot(800, lambda: self.web_view.load(QUrl(self.url)))
            else:
                self.web_view.setHtml(f"""
                <body style='background:#000;color:#fff;font-family:sans-serif;padding:60px;text-align:center;'>
                    <h2 style='color:#ff4d5e;margin-bottom:12px;'>前端控制台加载失败</h2>
                    <p style='color:#94a3b8;margin-bottom:24px;'>服务地址: {self.url}</p>
                    <button onclick='window.location.reload()' style='padding:10px 24px;background:#00f5a0;color:#052e16;font-size:14px;font-weight:bold;border:none;border-radius:6px;cursor:pointer;'>刷新页面 (F5)</button>
                </body>
                """)

    def center_window(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def setup_web_settings(self) -> None:
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)

    def setup_shortcuts(self) -> None:
        # F12 开发者工具
        f12_shortcut = QShortcut(QKeySequence("F12"), self)
        f12_shortcut.activated.connect(self.toggle_devtools)

        # Ctrl+R / F5 刷新
        f5_shortcut = QShortcut(QKeySequence("F5"), self)
        f5_shortcut.activated.connect(self.web_view.reload)

    def toggle_devtools(self) -> None:
        if self.devtools_window and self.devtools_window.isVisible():
            self.devtools_window.close()
            return
        if not self.devtools_window:
            self.devtools_window = QMainWindow(self)
            self.devtools_window.setWindowTitle("CQ 开发者调试控制台")
            self.devtools_window.resize(900, 600)
            dev_view = QWebEngineView()
            self.web_view.page().setDevToolsPage(dev_view.page())
            self.devtools_window.setCentralWidget(dev_view)
        self.devtools_window.show()
        self.devtools_window.raise_()

    def setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip("CHRONO QUANT · 量化交易终端")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(
            "QMenu { background-color: #12161c; color: #ffffff; border: 1px solid rgba(255,255,255,0.15); padding: 5px; }"
            "QMenu::item:selected { background-color: rgba(0,245,160,0.2); color: #00f5a0; }"
        )

        show_act = QAction("显示主窗口", self)
        show_act.triggered.connect(self.show_normal_and_raise)
        tray_menu.addAction(show_act)

        tray_menu.addSeparator()

        engine_act = QAction("启停量化引擎", self)
        engine_act.triggered.connect(self.toggle_engine)
        tray_menu.addAction(engine_act)

        data_act = QAction("打开数据目录 (data/)", self)
        data_act.triggered.connect(lambda: os.startfile(str(DATA_DIR)))
        tray_menu.addAction(data_act)

        dev_act = QAction("开发者调试工具 (F12)", self)
        dev_act.triggered.connect(self.toggle_devtools)
        tray_menu.addAction(dev_act)

        tray_menu.addSeparator()

        quit_act = QAction("完全退出", self)
        quit_act.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_act)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def show_normal_and_raise(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_normal_and_raise()

    def toggle_engine(self) -> None:
        if self.engine_worker and self.engine_worker.is_running:
            self.engine_worker.stop()
            self.tray.showMessage("CHRONO QUANT", "量化调度引擎已暂停", QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            self.engine_worker = EngineWorkerThread(source="synthetic", interval=30.0)
            self.engine_worker.start()
            self.tray.showMessage("CHRONO QUANT", "量化调度引擎已启动 (每 30 秒轮询)", QSystemTrayIcon.MessageIcon.Information, 2000)

    def closeEvent(self, event) -> None:
        if hasattr(self, "tray") and self.tray.isVisible():
            self.hide()
            self.tray.showMessage("CHRONO QUANT", "量化终端已最小化到系统托盘，后台服务继续运行", QSystemTrayIcon.MessageIcon.Information, 2000)
            event.ignore()
        else:
            self.quit_application()

    def quit_application(self) -> None:
        if self.engine_worker and self.engine_worker.is_running:
            self.engine_worker.stop()
        if self.server_thread:
            self.server_thread.stop()
        if hasattr(self, "tray"):
            self.tray.hide()
        QApplication.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="CHRONO QUANT 原生桌面客户端")
    parser.add_argument("--port", type=int, default=8818, help="内嵌本地 Web/API 端口 (默认 8818)")
    parser.add_argument("--engine", choices=["synthetic", "okx", "none"], default="synthetic", help="量化引擎启动模式 (默认 synthetic 仿真)")
    parser.add_argument("--interval", type=float, default=30.0, help="量化引擎轮询间隔秒数 (默认 30)")
    args, unknown = parser.parse_known_args()

    port = find_free_port(args.port)
    print(f">>> [1/3] 正在启动内嵌 FastAPI & 静态前端服务 (http://127.0.0.1:{port})...")
    server_thread = UvicornServerThread("127.0.0.1", port)
    server_thread.start()

    # 等待本地 HTTP 服务与 FastAPI 就绪
    url = f"http://127.0.0.1:{port}/"
    ready = False
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    log_debug(f"Starting health check probe loop on {url}api/v1/health ...")
    for i in range(60):
        if server_thread.error:
            log_debug(f"Server thread error detected at probe {i}: {server_thread.error}")
            break
        time.sleep(0.1)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health")
            with opener.open(req, timeout=0.5) as r:
                if r.status == 200:
                    ready = True
                    log_debug(f"Health probe SUCCEEDED at probe {i}!")
                    break
        except Exception as e:
            pass

    log_debug(f"Health probe finished: ready={ready}, error={server_thread.error}")

    app = QApplication.instance() or QApplication(sys.argv)
    QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    app.setApplicationName("CHRONO QUANT")
    app.setQuitOnLastWindowClosed(False)

    if not ready and server_thread.error:
        QMessageBox.critical(
            None,
            "CHRONO QUANT 启动错误",
            f"后台量化服务启动异常:\n{server_thread.error}\n\n请检查数据配置与系统环境。",
        )
        return 1

    engine_worker = None
    if args.engine != "none":
        print(f">>> [2/3] 正在启动后台量化引擎 (模式: {args.engine}, 轮询: {args.interval}s)...")
        engine_worker = EngineWorkerThread(source=args.engine, interval=args.interval)
        engine_worker.start()
    else:
        print(">>> [2/3] 已跳过量化引擎启动 (--engine=none)")

    print(">>> [3/3] 正在拉起原生桌面终端窗口 (PySide6 Chromium)...")
    window = MainWindow(url, engine_worker, server_thread)
    window.show()

    ret = app.exec()

    # 退出清理
    if engine_worker and engine_worker.is_running:
        engine_worker.stop()
    server_thread.stop()
    return ret


if __name__ == "__main__":
    sys.exit(main())
