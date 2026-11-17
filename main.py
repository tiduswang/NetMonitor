# -*- coding: utf-8 -*-
"""NetMonitor 入口：本机应用网络连接监测工具。"""
import sys

from PySide6.QtWidgets import QApplication

from collector import Collector
from gui import MainWindow
from storage import Storage


def main():
    storage = Storage()
    collector = Collector(storage, interval=1.0)
    collector.start()

    app = QApplication(sys.argv)
    # 关闭窗口不退出程序（改为隐藏到托盘），退出只能通过托盘菜单
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow(storage, collector)
    win.show()
    code = app.exec()

    collector.stop()
    collector.join(timeout=3)
    storage.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
