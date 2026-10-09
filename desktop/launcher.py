"""Source-run application entry point."""

from __future__ import annotations

import logging
import sys

from desktop.runtime import DesktopRuntime
from infrastructure.logging_setup import setup_logging
from infrastructure.paths import data_path, resource_path


def main() -> int:
    try:
        from PySide6.QtCore import QLockFile, Qt
        from PySide6.QtGui import QColor, QIcon, QPixmap
        from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
    except ImportError:
        print("PySide6 is required. Install requirements.txt.", file=sys.stderr)
        return 2

    app = QApplication(sys.argv)
    app.setApplicationName("科研统计工作台")
    app.setOrganizationName("ResearchWorkbench")
    app.setQuitOnLastWindowClosed(True)
    app_icon = QIcon(str(resource_path("desktop_assets", "icons", "rsw-app-icon.png")))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    runtime = DesktopRuntime()
    data_path("logs").mkdir(parents=True, exist_ok=True)
    data_path("runtime").mkdir(parents=True, exist_ok=True)
    setup_logging(level=logging.INFO)

    lock = QLockFile(str(data_path("runtime", "desktop.lock")))
    lock.setStaleLockTime(30_000)
    if not lock.tryLock(100):
        QMessageBox.information(None, "科研统计工作台", "应用已经在运行。")
        return 0

    splash_image = QPixmap(560, 260)
    splash_image.fill(QColor("#10243e"))
    splash = QSplashScreen(splash_image)
    splash.showMessage(
        "正在启动本地统计运行时与工作台…",
        Qt.AlignHCenter | Qt.AlignBottom,
        QColor("white"),
    )
    splash.show()
    app.processEvents()
    try:
        status = runtime.start()
        from desktop.window import DesktopWindow

        window = DesktopWindow(
            bootstrap_url=runtime.bootstrap_url,
            web_origin=status.web_url,
        )
        app.aboutToQuit.connect(runtime.stop)
        window.show()
        splash.finish(window)
        return app.exec()
    except Exception as exc:
        runtime.stop()
        logging.getLogger(__name__).exception("Desktop startup failed")
        splash.close()
        QMessageBox.critical(None, "启动失败", str(exc))
        return 1
    finally:
        runtime.stop()
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
