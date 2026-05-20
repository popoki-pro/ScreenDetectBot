#!/usr/bin/env python3
import sys
import time
from datetime import datetime
from typing import Optional

import numpy as np
import requests
import mss

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTextEdit, QGroupBox, QRubberBand,
    QListWidget, QSpinBox,
)
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, pyqtSignal, QObject, QThread, QTimer
from PyQt5.QtGui import QPainter, QColor, QPixmap


class AreaSelector(QWidget):
    area_selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self, screenshot: QPixmap):
        super().__init__()
        self._screenshot = screenshot
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(Qt.CrossCursor)
        self._origin = QPoint()
        self._selecting = False
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.pos()
            self.rubber_band.setGeometry(QRect(self._origin, QSize()))
            self.rubber_band.show()
            self._selecting = True

    def mouseMoveEvent(self, event):
        if self._selecting:
            self.rubber_band.setGeometry(QRect(self._origin, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._selecting:
            self._selecting = False
            self.rubber_band.hide()
            rect = QRect(self._origin, event.pos()).normalized()
            if rect.width() > 5 and rect.height() > 5:
                self.area_selected.emit(rect)
            else:
                self.cancelled.emit()
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))


class MonitorWorker(QObject):
    log_signal = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, area: QRect, bot_token: str, chat_ids: list,
                 message: str = "Changed", interval: int = 10):
        super().__init__()
        self.area = area
        self.bot_token = bot_token
        self.chat_ids = chat_ids
        self.message = message
        self.interval = interval
        self._running = False
        self.prev_image = None

    def start(self):
        self._running = True
        monitor = {
            "top": self.area.y(),
            "left": self.area.x(),
            "width": self.area.width(),
            "height": self.area.height(),
        }

        try:
            with mss.mss() as sct:
                self.log_signal.emit("Capturing initial frame...")
                self.prev_image = np.array(sct.grab(monitor))
                self.log_signal.emit(
                    f"Monitoring started. Checking every {self.interval} seconds."
                )

                while self._running:
                    for _ in range(self.interval * 10):
                        if not self._running:
                            break
                        time.sleep(0.1)

                    if not self._running:
                        break

                    current = np.array(sct.grab(monitor))
                    diff = np.abs(current.astype(np.int32) - self.prev_image.astype(np.int32))
                    changed_pixels = int(np.sum(diff.max(axis=2) > 10))

                    if changed_pixels > 0:
                        self.log_signal.emit(
                            f"Change detected! ({changed_pixels} pixels changed) "
                            f"Sending to {len(self.chat_ids)} chat(s)..."
                        )
                        self._send_to_all(self.message)
                    else:
                        self.log_signal.emit("No change detected.")

                    self.prev_image = current

        except Exception as e:
            self.log_signal.emit(f"Monitor error: {e}")

        self.finished.emit()

    def stop(self):
        self._running = False

    def _send_to_all(self, message: str):
        for chat_id in self.chat_ids:
            self._send_telegram(chat_id, message)

    def _send_telegram(self, chat_id: str, message: str):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
            if resp.ok:
                self.log_signal.emit(f"Sent to {chat_id}.")
            else:
                self.log_signal.emit(
                    f"Failed ({chat_id}): {resp.json().get('description', resp.text)}"
                )
        except Exception as e:
            self.log_signal.emit(f"Error ({chat_id}): {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Change Detector")
        self.setMinimumSize(560, 580)
        self.selected_area: Optional[QRect] = None
        self.worker: Optional[MonitorWorker] = None
        self.thread: Optional[QThread] = None
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # ── Telegram Settings ─────────────────────────────────────────────────
        tg_box = QGroupBox("Telegram Settings")
        tg_layout = QVBoxLayout(tg_box)

        # Bot token
        token_row = QHBoxLayout()
        token_lbl = QLabel("Bot Token:")
        token_lbl.setFixedWidth(72)
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("e.g. 123456789:ABCdef...")
        self.token_input.setEchoMode(QLineEdit.Password)
        token_row.addWidget(token_lbl)
        token_row.addWidget(self.token_input)
        tg_layout.addLayout(token_row)

        # Chat ID list
        tg_layout.addWidget(QLabel("Chat IDs:"))

        add_row = QHBoxLayout()
        self.chat_id_input = QLineEdit()
        self.chat_id_input.setPlaceholderText("Enter Chat ID (e.g. 123456789) then press Add")
        self.add_chat_btn = QPushButton("Add")
        self.add_chat_btn.setFixedWidth(60)
        add_row.addWidget(self.chat_id_input)
        add_row.addWidget(self.add_chat_btn)
        tg_layout.addLayout(add_row)

        list_row = QHBoxLayout()
        self.chat_list = QListWidget()
        self.chat_list.setFixedHeight(80)
        self.remove_chat_btn = QPushButton("Remove Selected")
        self.remove_chat_btn.setFixedWidth(120)
        self.remove_chat_btn.setEnabled(False)
        list_row.addWidget(self.chat_list)
        list_row.addWidget(self.remove_chat_btn, alignment=Qt.AlignTop)
        tg_layout.addLayout(list_row)

        # Message content
        msg_row = QHBoxLayout()
        msg_lbl = QLabel("Message:")
        msg_lbl.setFixedWidth(72)
        self.message_input = QLineEdit("Changed")
        self.message_input.setPlaceholderText("Message to send on detection")
        msg_row.addWidget(msg_lbl)
        msg_row.addWidget(self.message_input)
        tg_layout.addLayout(msg_row)

        # Detection interval
        interval_row = QHBoxLayout()
        interval_lbl = QLabel("Interval:")
        interval_lbl.setFixedWidth(72)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(10)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.setFixedWidth(90)
        interval_row.addWidget(interval_lbl)
        interval_row.addWidget(self.interval_spin)
        interval_row.addStretch()
        tg_layout.addLayout(interval_row)

        # Signals
        self.add_chat_btn.clicked.connect(self._on_add_chat)
        self.chat_id_input.returnPressed.connect(self._on_add_chat)
        self.remove_chat_btn.clicked.connect(self._on_remove_chat)
        self.chat_list.itemSelectionChanged.connect(
            lambda: self.remove_chat_btn.setEnabled(bool(self.chat_list.selectedItems()))
        )

        root.addWidget(tg_box)

        # ── Selected area info ────────────────────────────────────────────────
        self.area_label = QLabel("No area selected")
        self.area_label.setAlignment(Qt.AlignCenter)
        self.area_label.setStyleSheet(
            "border: 1px solid #aaa; border-radius: 4px; padding: 6px; color: #555;"
        )
        root.addWidget(self.area_label)

        # ── Control buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.select_btn = QPushButton("Select Area")
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

        for btn, slot in [
            (self.select_btn, self._on_select_area),
            (self.start_btn, self._on_start),
            (self.stop_btn, self._on_stop),
        ]:
            btn.setMinimumHeight(38)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)

        root.addLayout(btn_row)

        # ── Log ───────────────────────────────────────────────────────────────
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        log_layout.addWidget(self.log)
        root.addWidget(log_box)

    # ── chat id list ──────────────────────────────────────────────────────────

    def _on_add_chat(self):
        chat_id = self.chat_id_input.text().strip()
        if not chat_id:
            return
        existing = [self.chat_list.item(i).text() for i in range(self.chat_list.count())]
        if chat_id in existing:
            self._log(f"Chat ID {chat_id} is already in the list.")
            return
        self.chat_list.addItem(chat_id)
        self.chat_id_input.clear()

    def _on_remove_chat(self):
        for item in self.chat_list.selectedItems():
            self.chat_list.takeItem(self.chat_list.row(item))

    # ── area selection ────────────────────────────────────────────────────────

    def _on_select_area(self):
        self.hide()
        QTimer.singleShot(300, self._show_selector)

    def _show_selector(self):
        screen = QApplication.primaryScreen()
        screenshot = screen.grabWindow(0)
        self._selector = AreaSelector(screenshot)
        self._selector.area_selected.connect(self._on_area_selected)
        self._selector.cancelled.connect(self._restore_window)
        self._selector.show()
        self._selector.activateWindow()

    def _on_area_selected(self, rect: QRect):
        self.selected_area = rect
        self.area_label.setText(
            f"Selected: ({rect.x()}, {rect.y()})  {rect.width()} × {rect.height()} px"
        )
        self.area_label.setStyleSheet(
            "border: 1px solid #4a9; border-radius: 4px; padding: 6px; color: #333;"
        )
        self.start_btn.setEnabled(True)
        self._restore_window()
        self._log("Area selected.")

    def _restore_window(self):
        self.show()
        self.activateWindow()
        self.raise_()

    # ── monitoring ────────────────────────────────────────────────────────────

    def _on_start(self):
        token = self.token_input.text().strip()
        chat_ids = [self.chat_list.item(i).text() for i in range(self.chat_list.count())]
        message = self.message_input.text().strip() or "Changed"
        interval = self.interval_spin.value()

        if not token:
            self._log("Enter a Bot Token before starting.")
            return
        if not chat_ids:
            self._log("Add at least one Chat ID before starting.")
            return
        if not self.selected_area:
            self._log("Select a screen area first.")
            return

        self.worker = MonitorWorker(self.selected_area, token, chat_ids, message, interval)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.worker.log_signal.connect(self._log)
        self.worker.finished.connect(self._on_monitor_finished)
        self.thread.started.connect(self.worker.start)
        self.thread.start()

        self.select_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
        self.stop_btn.setEnabled(False)
        self._log("Stop requested...")

    def _on_monitor_finished(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.select_btn.setEnabled(True)
        self.start_btn.setEnabled(bool(self.selected_area))
        self.stop_btn.setEnabled(False)
        self._log("Monitoring stopped.")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
