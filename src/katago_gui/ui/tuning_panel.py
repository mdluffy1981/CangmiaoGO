from __future__ import annotations

import copy
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..i18n_tuning import tt
from ..settings import Settings
from ..tuning import Tuner, TuningCancelled, recent_result

# Keeps Python references alive until each QThread has stopped.
_workers: set["_TuningWorker"] = set()


class _TuningWorker(QThread):
    progress = Signal(str)
    failed = Signal(str)
    applied = Signal(str)

    def __init__(self, settings: Settings, restore: bool = False):
        super().__init__()
        self.restore = restore
        self.tuner = Tuner(settings, self.progress.emit)

    def run(self) -> None:
        try:
            if self.restore:
                self.applied.emit(self.tuner.restore())
            else:
                result = self.tuner.run()
                if result.config:
                    self.applied.emit(result.config)
        except TuningCancelled as exc:
            self.progress.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or tt("调优线程意外失败：{0}", type(exc).__name__))


class TuningPanel(QWidget):
    message = Signal(str)
    error = Signal(str)
    running_changed = Signal(bool)
    config_applied = Signal(str)

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.start_guard: Callable[[], bool] | None = None
        self._worker: _TuningWorker | None = None
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.recent = QLabel()
        self.recent.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.start_button = QPushButton()
        self.cancel_button = QPushButton()
        self.restore_button = QPushButton()
        self.cancel_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.cancel)
        self.restore_button.clicked.connect(self.restore)
        buttons = QHBoxLayout()
        for button in (self.start_button, self.cancel_button, self.restore_button):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(self.recent)
        layout.addLayout(buttons)
        layout.addWidget(self.log)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.info.setText(tt("调优前请停止对局搜索或分析。测试有时间限制，不保证性能提升。"))
        self.recent.setText(recent_result(self.settings))
        self.start_button.setText(tt("开始调优"))
        self.cancel_button.setText(tt("取消调优"))
        self.restore_button.setText(tt("恢复上次配置"))

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self) -> None:
        self._launch(False)

    def restore(self) -> None:
        self._launch(True)

    def _launch(self, restore: bool) -> None:
        if self.is_running or (self.start_guard is not None and not self.start_guard()):
            return
        worker = _TuningWorker(copy.deepcopy(self.settings), restore)
        self._worker = worker
        _workers.add(worker)
        worker.progress.connect(self._message)
        worker.failed.connect(self._error)
        worker.applied.connect(self._applied)
        worker.finished.connect(self._finished)
        self._set_buttons(True)
        self.running_changed.emit(True)
        worker.start()

    def _set_buttons(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.restore_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    def _message(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.message.emit(text)

    def _error(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.error.emit(text)

    def _applied(self, config: str) -> None:
        if self._worker is not None and not self._worker.tuner.cancelled:
            self.config_applied.emit(config)

    def _finished(self) -> None:
        worker = self._worker
        if worker is None or worker.isRunning():
            return
        self._worker = None
        _workers.discard(worker)
        worker.deleteLater()
        self.recent.setText(recent_result(self.settings))
        self._set_buttons(False)
        self.running_changed.emit(False)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.tuner.cancel()
            self.cancel_button.setEnabled(False)
            self._message(tt("已请求取消，正在清理引擎进程…"))

    def wait_for_finished(self, timeout_ms: int = 5000) -> bool:
        """Block until the worker stops; queued results are delivered before running_changed(False)."""
        worker = self._worker
        if worker is None:
            return True
        if not worker.wait(max(0, timeout_ms)):
            return False
        QCoreApplication.sendPostedEvents(self, QEvent.Type.MetaCall.value)
        self._finished()
        return True

    def closeEvent(self, event) -> None:
        self.cancel()
        if self.wait_for_finished():
            super().closeEvent(event)
        else:
            event.ignore()
