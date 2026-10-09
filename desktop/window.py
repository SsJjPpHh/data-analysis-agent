"""PySide6 window hosting the existing web workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import QFileDialog, QMainWindow

from infrastructure.paths import data_path


class LocalPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, allowed_origin: str, parent=None):
        super().__init__(profile, parent)
        self.allowed_origin = self._origin(QUrl(allowed_origin))

    @staticmethod
    def _origin(url: QUrl) -> tuple[str, str, int]:
        port = url.port()
        if port < 0:
            port = 443 if url.scheme().lower() == "https" else 80
        return url.scheme().lower(), url.host().lower(), port

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        if not is_main_frame or url.scheme() in {"blob", "data"}:
            return True
        if self._origin(url) == self.allowed_origin:
            return True
        QDesktopServices.openUrl(url)
        return False


class DesktopWindow(QMainWindow):
    def __init__(self, *, bootstrap_url: str, web_origin: str):
        super().__init__()
        self.setWindowTitle("科研统计工作台")
        self.resize(1440, 920)
        # QObject destroys children in creation order. Release the view/page
        # before its custom profile, which must outlive every associated page.
        self.view = QWebEngineView(self)
        profile = QWebEngineProfile("ResearchWorkbench", self)
        profile.setPersistentStoragePath(str(data_path("runtime", "web-profile")))
        profile.setCachePath(str(data_path("runtime", "web-cache")))
        profile.downloadRequested.connect(self._download_requested)
        self.view.setPage(LocalPage(profile, web_origin, self.view))
        from desktop.local_import import create_desktop_import_bridge

        self.web_channel = QWebChannel(self.view.page())
        self.desktop_import = create_desktop_import_bridge(self)
        self.web_channel.registerObject("desktopImport", self.desktop_import)
        self.view.page().setWebChannel(self.web_channel)
        self.setCentralWidget(self.view)
        self.view.load(QUrl(bootstrap_url))

    def _download_requested(self, download) -> None:
        suggested = download.downloadFileName() or "export"
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "保存导出文件",
            str(data_path("exports", Path(suggested).name)),
        )
        if not target:
            download.cancel()
            return
        path = Path(target)
        download.setDownloadDirectory(str(path.parent))
        download.setDownloadFileName(path.name)
        download.accept()
