# -*- coding: utf-8 -*-
"""验证托盘与关闭行为：
1) 点击关闭按钮 -> 窗口隐藏、程序不退出、采集继续；
2) 托盘菜单"退出" -> 程序真正退出。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from collector import Collector
from gui import MainWindow
from storage import Storage

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "verify_tray.db")
for suf in ("", "-wal", "-shm"):
    p = TMP + suf
    if os.path.exists(p):
        os.remove(p)

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

storage = Storage(db_path=TMP)
collector = Collector(storage, interval=1.0)
collector.start()
win = MainWindow(storage, collector)
win.show()

state = {"step": 0}


def check_tray():
    tray = win.tray
    assert tray is not None, "托盘不可用（isSystemTrayAvailable 返回 False）"
    acts = [a.text() for a in tray.contextMenu().actions() if not a.isSeparator()]
    print("[1] 托盘已创建，菜单项:", acts)
    assert "退出" in acts, "托盘菜单缺少 退出"


def do_close():
    print("[2] 模拟点击窗口关闭按钮 (win.close())")
    win.close()


def after_close():
    print("    关闭后窗口可见:", win.isVisible())
    print("    采集仍在运行:", collector.stats()["running"],
          "| 记录数:", storage.total_count())
    assert not win.isVisible(), "关闭后窗口未隐藏"
    assert collector.stats()["running"], "关闭后采集线程停止了"
    assert storage.total_count() > 0, "关闭后无记录写入"
    print("[3] 关闭后程序未退出，采集继续 —— 通过")
    print("[4] 调用托盘菜单 退出 (win.quit_app())")
    win.quit_app()


QTimer.singleShot(800, check_tray)
QTimer.singleShot(1500, do_close)
QTimer.singleShot(3500, after_close)

code = app.exec()
collector.stop()
collector.join(timeout=3)
recorded = storage.total_count()
storage.close()
print("[5] app.exec() 已返回，退出码:", code, "| 最终记录数:", recorded)
for suf in ("", "-wal", "-shm"):
    p = TMP + suf
    if os.path.exists(p):
        os.remove(p)
print("[OK] 托盘与关闭行为验证通过")
