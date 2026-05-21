#!/usr/bin/env python3
import re
import sys
import time
from datetime import datetime
from typing import Optional

import mss
import pytesseract
import requests
from PIL import Image

import storage

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTextEdit, QGroupBox, QRubberBand,
    QListWidget, QSpinBox, QComboBox, QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, pyqtSignal, QObject, QThread, QTimer
from PyQt5.QtGui import QPainter, QColor, QPixmap


# ── Area selector overlay ─────────────────────────────────────────────────────

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


# ── Text matching ─────────────────────────────────────────────────────────────

MATCH_CONTAINS = "Contains"
MATCH_EXACT    = "Exact"
MATCH_REGEX    = "Regex"


def text_matches(detected: str, target: str, mode: str) -> bool:
    detected = detected.strip()
    if mode == MATCH_EXACT:
        return detected == target
    if mode == MATCH_CONTAINS:
        return target.lower() in detected.lower()
    if mode == MATCH_REGEX:
        try:
            return bool(re.search(target, detected))
        except re.error:
            return False
    return False


# ── Monitor worker ────────────────────────────────────────────────────────────

class TextMonitorWorker(QObject):
    log_signal           = pyqtSignal(str)
    last_detected_signal = pyqtSignal(str)
    finished             = pyqtSignal()

    def __init__(self, area: QRect, bot_token: str, chat_ids: list,
                 targets: list, match_mode: str, message: str, interval: int):
        super().__init__()
        self.area       = area
        self.bot_token  = bot_token
        self.chat_ids   = chat_ids
        self.targets    = targets
        self.match_mode = match_mode
        self.message    = message
        self.interval   = interval
        self._running   = False
        self._last_sent: Optional[str] = None

    def start(self):
        self._running = True
        monitor = {
            "top": self.area.y(), "left": self.area.x(),
            "width": self.area.width(), "height": self.area.height(),
        }
        self.log_signal.emit(f"Monitoring started. Checking every {self.interval} seconds.")

        try:
            with mss.mss() as sct:
                while self._running:
                    for _ in range(self.interval * 10):
                        if not self._running:
                            break
                        time.sleep(0.1)

                    if not self._running:
                        break

                    raw     = sct.grab(monitor)
                    img     = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    clean   = pytesseract.image_to_string(img).strip()
                    matched = [t for t in self.targets if text_matches(clean, t, self.match_mode)]

                    if matched:
                        if clean == self._last_sent:
                            self.log_signal.emit("Matched but already sent — skipping.")
                        else:
                            payload = self.message if self.message else clean
                            self.log_signal.emit(
                                f"Matched: {matched!r} — sending to {len(self.chat_ids)} chat(s)..."
                            )
                            self._send_to_all(payload)
                            self._last_sent = clean
                            self.last_detected_signal.emit(clean)
                    else:
                        preview = clean[:60].replace("\n", " ")
                        self.log_signal.emit(f'No match. OCR: "{preview}"')

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
            resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
            if resp.ok:
                self.log_signal.emit(f"Sent to {chat_id}.")
            else:
                self.log_signal.emit(
                    f"Failed ({chat_id}): {resp.json().get('description', resp.text)}"
                )
        except Exception as e:
            self.log_signal.emit(f"Error ({chat_id}): {e}")


# ── Settings dialog ───────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, main_window: "MainWindow"):
        super().__init__(main_window)
        self._main = main_window
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self.setModal(True)

        # working copy of area (committed on OK)
        self._area: Optional[QRect] = main_window.selected_area

        self._build_ui()
        self._populate(main_window._cfg)

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # ── Telegram ──────────────────────────────────────────────────────────
        tg_box    = QGroupBox("Telegram")
        tg_layout = QVBoxLayout(tg_box)

        token_row = QHBoxLayout()
        lbl = QLabel("Bot Token:")
        lbl.setFixedWidth(90)
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("123456789:ABCdef...")
        self.token_input.setEchoMode(QLineEdit.Password)
        token_row.addWidget(lbl)
        token_row.addWidget(self.token_input)
        tg_layout.addLayout(token_row)

        tg_layout.addWidget(QLabel("Chat IDs:"))

        add_chat_row = QHBoxLayout()
        self.chat_id_input = QLineEdit()
        self.chat_id_input.setPlaceholderText("Enter Chat ID then press Add")
        self.add_chat_btn = QPushButton("Add")
        self.add_chat_btn.setFixedWidth(60)
        add_chat_row.addWidget(self.chat_id_input)
        add_chat_row.addWidget(self.add_chat_btn)
        tg_layout.addLayout(add_chat_row)

        chat_list_row = QHBoxLayout()
        self.chat_list = QListWidget()
        self.chat_list.setFixedHeight(72)
        self.remove_chat_btn = QPushButton("Remove")
        self.remove_chat_btn.setFixedWidth(80)
        self.remove_chat_btn.setEnabled(False)
        chat_list_row.addWidget(self.chat_list)
        chat_list_row.addWidget(self.remove_chat_btn, alignment=Qt.AlignTop)
        tg_layout.addLayout(chat_list_row)

        msg_row = QHBoxLayout()
        msg_lbl = QLabel("Message:")
        msg_lbl.setFixedWidth(90)
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Leave empty to send detected text")
        msg_row.addWidget(msg_lbl)
        msg_row.addWidget(self.message_input)
        tg_layout.addLayout(msg_row)

        interval_row = QHBoxLayout()
        int_lbl = QLabel("Interval:")
        int_lbl.setFixedWidth(90)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(10)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.setFixedWidth(90)
        interval_row.addWidget(int_lbl)
        interval_row.addWidget(self.interval_spin)
        interval_row.addStretch()
        tg_layout.addLayout(interval_row)

        self.add_chat_btn.clicked.connect(self._on_add_chat)
        self.chat_id_input.returnPressed.connect(self._on_add_chat)
        self.remove_chat_btn.clicked.connect(self._on_remove_chat)
        self.chat_list.itemSelectionChanged.connect(
            lambda: self.remove_chat_btn.setEnabled(bool(self.chat_list.selectedItems()))
        )

        root.addWidget(tg_box)

        # ── Detection ─────────────────────────────────────────────────────────
        det_box    = QGroupBox("Text Detection")
        det_layout = QVBoxLayout(det_box)

        mode_row = QHBoxLayout()
        mode_lbl = QLabel("Match Mode:")
        mode_lbl.setFixedWidth(90)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MATCH_CONTAINS, MATCH_EXACT, MATCH_REGEX])
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        det_layout.addLayout(mode_row)

        det_layout.addWidget(QLabel("Target Texts (any match triggers the alert):"))

        add_target_row = QHBoxLayout()
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("Enter text to watch for, then press Add")
        self.add_target_btn = QPushButton("Add")
        self.add_target_btn.setFixedWidth(60)
        add_target_row.addWidget(self.target_input)
        add_target_row.addWidget(self.add_target_btn)
        det_layout.addLayout(add_target_row)

        target_list_row = QHBoxLayout()
        self.target_list = QListWidget()
        self.target_list.setFixedHeight(80)
        self.remove_target_btn = QPushButton("Remove")
        self.remove_target_btn.setFixedWidth(80)
        self.remove_target_btn.setEnabled(False)
        target_list_row.addWidget(self.target_list)
        target_list_row.addWidget(self.remove_target_btn, alignment=Qt.AlignTop)
        det_layout.addLayout(target_list_row)

        self.add_target_btn.clicked.connect(self._on_add_target)
        self.target_input.returnPressed.connect(self._on_add_target)
        self.remove_target_btn.clicked.connect(self._on_remove_target)
        self.target_list.itemSelectionChanged.connect(
            lambda: self.remove_target_btn.setEnabled(bool(self.target_list.selectedItems()))
        )

        root.addWidget(det_box)

        # ── Area selection ────────────────────────────────────────────────────
        area_box    = QGroupBox("Screen Area")
        area_layout = QHBoxLayout(area_box)

        self.area_label = QLabel("No area selected")
        self.area_label.setStyleSheet("color: #666;")
        self.select_area_btn = QPushButton("Select Area")
        self.select_area_btn.setFixedWidth(110)
        self.select_area_btn.clicked.connect(self._on_select_area)
        area_layout.addWidget(self.area_label, stretch=1)
        area_layout.addWidget(self.select_area_btn)

        root.addWidget(area_box)

        # ── OK / Cancel ───────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── populate from cfg ─────────────────────────────────────────────────────

    def _populate(self, cfg: dict):
        self.token_input.setText(cfg.get("token", ""))
        self.chat_list.clear()
        for cid in cfg.get("chat_ids", []):
            self.chat_list.addItem(cid)
        self.message_input.setText(cfg.get("message", ""))
        self.interval_spin.setValue(cfg.get("interval", 10))
        idx = self.mode_combo.findText(cfg.get("match_mode", MATCH_CONTAINS))
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.target_list.clear()
        for t in cfg.get("targets", []):
            self.target_list.addItem(t)
        self._refresh_area_label()

    # ── chat list ─────────────────────────────────────────────────────────────

    def _on_add_chat(self):
        cid = self.chat_id_input.text().strip()
        if not cid:
            return
        existing = [self.chat_list.item(i).text() for i in range(self.chat_list.count())]
        if cid not in existing:
            self.chat_list.addItem(cid)
        self.chat_id_input.clear()

    def _on_remove_chat(self):
        for item in self.chat_list.selectedItems():
            self.chat_list.takeItem(self.chat_list.row(item))

    # ── target list ───────────────────────────────────────────────────────────

    def _on_add_target(self):
        text = self.target_input.text().strip()
        if not text:
            return
        existing = [self.target_list.item(i).text() for i in range(self.target_list.count())]
        if text not in existing:
            self.target_list.addItem(text)
        self.target_input.clear()

    def _on_remove_target(self):
        for item in self.target_list.selectedItems():
            self.target_list.takeItem(self.target_list.row(item))

    # ── area selection ────────────────────────────────────────────────────────

    def _on_select_area(self):
        self.hide()
        self._main.hide()
        QTimer.singleShot(300, self._show_selector)

    def _show_selector(self):
        screenshot = QApplication.primaryScreen().grabWindow(0)
        self._selector = AreaSelector(screenshot)
        self._selector.area_selected.connect(self._on_area_selected)
        self._selector.cancelled.connect(self._restore)
        self._selector.show()
        self._selector.activateWindow()

    def _on_area_selected(self, rect: QRect):
        self._area = rect
        self._refresh_area_label()
        self._restore()

    def _restore(self):
        self._main.show()
        self._main.activateWindow()
        self.show()
        self.activateWindow()
        self.raise_()

    def _refresh_area_label(self):
        if self._area:
            r = self._area
            self.area_label.setText(
                f"({r.x()}, {r.y()})  {r.width()} × {r.height()} px"
            )
            self.area_label.setStyleSheet("color: #333;")
        else:
            self.area_label.setText("No area selected")
            self.area_label.setStyleSheet("color: #666;")

    # ── accept ────────────────────────────────────────────────────────────────

    def _on_ok(self):
        cfg = {
            "token":      self.token_input.text().strip(),
            "chat_ids":   [self.chat_list.item(i).text() for i in range(self.chat_list.count())],
            "message":    self.message_input.text().strip(),
            "interval":   self.interval_spin.value(),
            "match_mode": self.mode_combo.currentText(),
            "targets":    [self.target_list.item(i).text() for i in range(self.target_list.count())],
        }
        self._main._cfg          = cfg
        self._main.selected_area = self._area
        self._main._refresh_start_btn()
        self.accept()


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Text Detector")
        self.setMinimumSize(420, 300)

        self.selected_area: Optional[QRect] = None
        self.worker: Optional[TextMonitorWorker] = None
        self.thread: Optional[QThread] = None
        self._last_detected: str = ""
        self._cfg: dict = {
            "token": "", "chat_ids": [], "message": "",
            "interval": 10, "match_mode": MATCH_CONTAINS, "targets": [],
        }

        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.start_btn    = QPushButton("Start")
        self.stop_btn     = QPushButton("Stop")
        self.settings_btn = QPushButton("Settings")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

        for btn, slot in [
            (self.start_btn,    self._on_start),
            (self.stop_btn,     self._on_stop),
            (self.settings_btn, self._on_settings),
        ]:
            btn.setMinimumHeight(38)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)

        root.addLayout(btn_row)

        # ── Log ───────────────────────────────────────────────────────────────
        log_box    = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log   = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        root.addWidget(log_box)

    # ── settings ──────────────────────────────────────────────────────────────

    def _on_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec_()

    # ── monitoring ────────────────────────────────────────────────────────────

    def _on_start(self):
        cfg = self._cfg
        if not cfg["token"]:
            self._log("Open Settings and enter a Bot Token.")
            return
        if not cfg["chat_ids"]:
            self._log("Open Settings and add at least one Chat ID.")
            return
        if not cfg["targets"]:
            self._log("Open Settings and add at least one target text.")
            return
        if not self.selected_area:
            self._log("Open Settings and select a screen area.")
            return

        self.worker = TextMonitorWorker(
            self.selected_area,
            cfg["token"], cfg["chat_ids"], cfg["targets"],
            cfg["match_mode"], cfg["message"], cfg["interval"],
        )
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.worker.log_signal.connect(self._log)
        self.worker.last_detected_signal.connect(self._on_last_detected)
        self.worker.finished.connect(self._on_monitor_finished)
        self.thread.started.connect(self.worker.start)
        self.thread.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.settings_btn.setEnabled(False)

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
        self.stop_btn.setEnabled(False)
        self._log("Stop requested...")

    def _on_monitor_finished(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self._refresh_start_btn()
        self.stop_btn.setEnabled(False)
        self.settings_btn.setEnabled(True)
        self._log("Monitoring stopped.")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _on_last_detected(self, text: str):
        self._last_detected = text

    def _refresh_start_btn(self):
        cfg = self._cfg
        ready = (
            bool(self.selected_area)
            and bool(cfg.get("token"))
            and bool(cfg.get("chat_ids"))
            and bool(cfg.get("targets"))
        )
        self.start_btn.setEnabled(ready)

    def _load_settings(self):
        s = storage.load_settings()
        if s.get("BOT_TOKEN"):
            self._cfg["token"] = s["BOT_TOKEN"]
        if s.get("CHAT_IDS"):
            self._cfg["chat_ids"] = [c for c in s["CHAT_IDS"].split("|") if c]
        if s.get("MESSAGE") is not None:
            self._cfg["message"] = s["MESSAGE"]
        if s.get("INTERVAL"):
            try:
                self._cfg["interval"] = int(s["INTERVAL"])
            except ValueError:
                pass
        if s.get("MATCH_MODE"):
            self._cfg["match_mode"] = s["MATCH_MODE"]
        if s.get("TARGETS"):
            self._cfg["targets"] = [t for t in s["TARGETS"].split("|") if t]
        if s.get("LAST_DETECTED"):
            self._last_detected = s["LAST_DETECTED"]
        self._refresh_start_btn()

    def _save_settings(self):
        cfg = self._cfg
        storage.save_settings({
            "BOT_TOKEN":     cfg.get("token", ""),
            "CHAT_IDS":      "|".join(cfg.get("chat_ids", [])),
            "MESSAGE":       cfg.get("message", ""),
            "INTERVAL":      str(cfg.get("interval", 10)),
            "MATCH_MODE":    cfg.get("match_mode", MATCH_CONTAINS),
            "TARGETS":       "|".join(cfg.get("targets", [])),
            "LAST_DETECTED": self._last_detected,
        })

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        self._save_settings()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
