"""
授权请求对话框。
当跨 UID 的 mod 请求控制本机设备时，弹出此对话框供用户确认。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication,
)
from PyQt6.QtCore import Qt
import asyncio

from .. import i18n


class AuthRequestDialog(QDialog):

    def __init__(self, from_address: str, description: str, parent=None):
        super().__init__(parent)
        self._future: asyncio.Future | None = None
        self.setWindowTitle(i18n.translate("request.title"))
        self.setModal(True)
        self._build_ui(from_address, description)

    def _build_ui(self, from_address, description):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        icon_label = QLabel("⚠")
        icon_label.setStyleSheet("font-size: 32px;")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title = QLabel(i18n.translate("request.incoming_request"))
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        from_label = QLabel(i18n.translate("request.requester", address=from_address))
        from_label.setWordWrap(True)
        layout.addWidget(from_label)

        if description:
            desc_label = QLabel(i18n.translate("request.description", desc=description))
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        reject_btn = QPushButton(i18n.translate("request.reject"))
        reject_btn.clicked.connect(lambda: self._resolve(False))
        reject_btn.setMinimumHeight(36)

        approve_btn = QPushButton(i18n.translate("request.approve"))
        approve_btn.clicked.connect(lambda: self._resolve(True))
        approve_btn.setMinimumHeight(36)
        approve_btn.setDefault(True)

        btn_layout.addWidget(reject_btn)
        btn_layout.addWidget(approve_btn)
        layout.addLayout(btn_layout)

        self.setMinimumWidth(360)

    def _resolve(self, approved: bool):
        if self._future and not self._future.done():
            self._future.set_result(approved)
        self.close()

    def closeEvent(self, event):
        if self._future and not self._future.done():
            self._future.set_result(False)
        super().closeEvent(event)


async def show_auth_dialog(
    from_address: str, description: str,
) -> bool:
    """弹出授权确认对话框并异步等待用户选择。UI 模式使用。"""
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    from . import window as _main_window
    parent = _main_window if _main_window else None

    dialog = AuthRequestDialog(from_address, description, parent)
    dialog._future = future
    dialog.open()

    return await future


async def prompt_auth_cli(
    from_address: str, description: str,
) -> bool:
    """命令行授权确认。无 UI 模式使用。"""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _cli_input, from_address, description)
        return result
    except Exception:
        return False


def _cli_input(from_address: str, description: str) -> bool:
    print(f"\n{'=' * 50}")
    print(i18n.translate("request.incoming_request"))
    print(i18n.translate("request.requester", address=from_address))
    if description:
        print(i18n.translate("request.description", desc=description))
    print('=' * 50)
    answer = input(i18n.translate("request.cli_prompt")).strip().lower()
    return answer in ('y', 'yes')