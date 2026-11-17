# -*- coding: utf-8 -*-
"""PySide6 图形界面。

选 PySide6 而非 Tkinter 的原因：表格排序/筛选/大量行刷新性能更好，
样式现代，且与 PyInstaller 打包配合成熟。
"""
import ctypes
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QDateTime

import exporter

TABLE_COLUMNS = [
    ("进程名", "process_name"),
    ("PID", "pid"),
    ("远程IP", "remote_ip"),
    ("远程端口", "remote_port"),
    ("归属地", "ip_region"),
    ("协议", "protocol"),
    ("状态", "status"),
    ("本地端口", "local_port"),
    ("DNS域名", "dns_name"),
    ("首次发现", "first_seen"),
    ("最后活跃", "last_seen"),
    ("次数", "seen_count"),
    ("进程路径", "process_path"),
]

REFRESH_MS = 1000
PAGE_SIZE = 2000


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _make_tray_icon():
    """程序内绘制托盘图标（蓝底 + 三节点网络连线），无需外部资源文件。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2F6FED"))
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QPen(QColor("#FFFFFF"), 3))
    p.drawLine(18, 18, 46, 32)
    p.drawLine(46, 32, 18, 46)
    p.setBrush(QColor("#FFFFFF"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(11, 11, 14, 14)
    p.drawEllipse(39, 25, 14, 14)
    p.drawEllipse(11, 39, 14, 14)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, storage, collector):
        super().__init__()
        self.storage = storage
        self.collector = collector
        self.setWindowTitle("NetMonitor - 本机应用网络连接监测")
        self.resize(1280, 720)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)

        self._really_quit = False
        self._tray_hint_shown = False
        self._setup_tray()

        if not is_admin():
            self.statusBar().showMessage(
                "提示：当前非管理员权限运行，部分系统进程的名称/路径/连接可能无法获取。"
                "建议右键“以管理员身份运行”。")

    # ------------------------------------------------------------ 托盘
    def _setup_tray(self):
        """关闭窗口不退出，隐藏到托盘；只有托盘右键菜单“退出”才真正结束程序。"""
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(_make_tray_icon(), self)
        self.tray.setToolTip("NetMonitor - 正在后台采集")

        menu = QMenu(self)
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self.show_window)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        """菜单“退出”调用：标记真正退出并结束应用。"""
        self._really_quit = True
        self._timer.stop()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        # 系统计划任务/注销等场景下无托盘，直接正常退出，避免进程残留不可恢复
        if self._really_quit or self.tray is None:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(
                "NetMonitor 仍在后台运行",
                "窗口已隐藏，采集继续进行。\n右键任务栏托盘图标选择“退出”可完全关闭。",
                QSystemTrayIcon.Information, 4000)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- 统计概览
        stats_row = QHBoxLayout()
        self.lbl_apps = QLabel("活跃应用数: 0")
        self.lbl_conns = QLabel("活跃连接数: 0")
        self.lbl_total = QLabel("累计记录数: 0")
        self.lbl_state = QLabel("采集状态: 已停止")
        for w in (self.lbl_apps, self.lbl_conns, self.lbl_total, self.lbl_state):
            w.setStyleSheet("font-weight:bold; padding:4px 12px;")
            stats_row.addWidget(w)
        stats_row.addStretch(1)
        root.addLayout(stats_row)

        # ---- 筛选区
        filter_row = QHBoxLayout()
        self.ed_process = QLineEdit(); self.ed_process.setPlaceholderText("进程名")
        self.ed_ip = QLineEdit();       self.ed_ip.setPlaceholderText("远程IP")
        self.ed_port = QLineEdit();     self.ed_port.setPlaceholderText("端口")
        self.ed_port.setFixedWidth(80)
        self.cb_proto = QComboBox();    self.cb_proto.addItems(["全部", "TCP", "UDP"])
        self.ed_keyword = QLineEdit();  self.ed_keyword.setPlaceholderText("关键字(进程/IP/域名/归属地/路径)")

        self.ck_time = QCheckBox("时间范围")
        self.dt_from = QDateTimeEdit(QDateTime.currentDateTime().addSecs(-3600))
        self.dt_to = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_from.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_to.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_from.setEnabled(False); self.dt_to.setEnabled(False)
        self.ck_time.toggled.connect(self.dt_from.setEnabled)
        self.ck_time.toggled.connect(self.dt_to.setEnabled)

        for w in (self.ed_process, self.ed_ip, self.ed_port, self.cb_proto,
                  self.ed_keyword, self.ck_time, self.dt_from, self.dt_to):
            filter_row.addWidget(w)
        root.addLayout(filter_row)

        # ---- 控制按钮
        btn_row = QHBoxLayout()
        self.btn_toggle = QPushButton("暂停采集")
        self.btn_toggle.clicked.connect(self.toggle_collect)
        self.btn_clear = QPushButton("清空数据库")
        self.btn_clear.clicked.connect(self.clear_db)
        self.btn_export = QPushButton("导出 Excel")
        self.btn_export.clicked.connect(self.export_excel)
        btn_row.addWidget(self.btn_toggle)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_export)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # ---- 表格
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([t for t, _ in TABLE_COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table)

        # 筛选即时生效
        self.ed_process.textChanged.connect(self.refresh)
        self.ed_ip.textChanged.connect(self.refresh)
        self.ed_port.textChanged.connect(self.refresh)
        self.ed_keyword.textChanged.connect(self.refresh)
        self.cb_proto.currentTextChanged.connect(self.refresh)
        self.ck_time.toggled.connect(self.refresh)
        self.dt_from.dateTimeChanged.connect(self.refresh)
        self.dt_to.dateTimeChanged.connect(self.refresh)

    # ------------------------------------------------------------ 筛选参数
    def _filters(self):
        f = {
            "process": self.ed_process.text().strip(),
            "ip": self.ed_ip.text().strip(),
            "port": self.ed_port.text().strip(),
            "protocol": self.cb_proto.currentText(),
            "keyword": self.ed_keyword.text().strip(),
            "limit": PAGE_SIZE,
        }
        if self.ck_time.isChecked():
            f["time_from"] = self.dt_from.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            f["time_to"] = self.dt_to.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        return f

    # ------------------------------------------------------------ 刷新
    def refresh(self):
        # 统计概览
        st = self.collector.stats()
        self.lbl_apps.setText("活跃应用数: %d" % st["active_apps"])
        self.lbl_conns.setText("活跃连接数: %d" % st["active_conns"])
        self.lbl_total.setText("累计记录数: %d" % self.storage.total_count())
        running = st["running"] and not self.collector.paused
        self.lbl_state.setText(
            "采集状态: %s (%s)" % ("运行中" if running else "已暂停",
                                   st.get("backend") or "-"))

        # 表格
        rows = self.storage.query(**self._filters())
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            for c, (_t, key) in enumerate(TABLE_COLUMNS):
                val = rec.get(key, "")
                item = QTableWidgetItem()
                if isinstance(val, int):
                    item.setData(Qt.EditRole, val)   # 数字按数值排序
                else:
                    item.setText("" if val is None else str(val))
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(sorting)

    # ------------------------------------------------------------ 操作
    def toggle_collect(self):
        if self.collector.paused:
            self.collector.resume()
            self.btn_toggle.setText("暂停采集")
        else:
            self.collector.pause()
            self.btn_toggle.setText("继续采集")

    def clear_db(self):
        ret = QMessageBox.warning(
            self, "确认清空",
            "确定要清空数据库中全部记录吗？此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            ret2 = QMessageBox.question(
                self, "二次确认", "请再次确认：删除所有采集记录？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret2 == QMessageBox.Yes:
                self.storage.clear()
                self.refresh()

    def export_excel(self):
        choice = QMessageBox.question(
            self, "导出范围",
            "导出当前筛选结果？\n（选择 No 则导出数据库全部记录）",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if choice == QMessageBox.Cancel:
            return
        default_name = "net_monitor_%s.xlsx" % time.strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Excel", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            if choice == QMessageBox.Yes:
                f = self._filters()
                f.pop("limit", None)
                records = self.storage.query_export(**f)
            else:
                records = self.storage.query_export()
            exporter.export_xlsx(records, path)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        ret = QMessageBox.information(
            self, "导出成功",
            "已导出 %d 条记录到：\n%s\n\n是否立即打开文件？" % (len(records), path),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            os.startfile(path)
