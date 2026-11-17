# -*- coding: utf-8 -*-
"""Excel 导出：表头、时间格式、列宽、冻结首行。"""
import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COLUMNS = [
    ("进程名", "process_name", 22),
    ("PID", "pid", 8),
    ("远程IP", "remote_ip", 18),
    ("远程端口", "remote_port", 10),
    ("归属地", "ip_region", 24),
    ("协议", "protocol", 8),
    ("状态", "status", 14),
    ("本地端口", "local_port", 10),
    ("DNS域名", "dns_name", 30),
    ("首次发现时间", "first_seen", 20),
    ("最后活跃时间", "last_seen", 20),
    ("出现次数", "seen_count", 10),
    ("进程路径", "process_path", 50),
]

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")


def _parse_dt(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return s


def export_xlsx(records, path):
    """把记录列表导出为 xlsx，返回文件路径。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "网络连接记录"

    # 表头
    for col, (title, _key, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"

    # 数据
    for r, rec in enumerate(records, 2):
        for c, (_title, key, _w) in enumerate(COLUMNS, 1):
            val = rec.get(key, "")
            if key in ("first_seen", "last_seen"):
                dt = _parse_dt(val)
                cell = ws.cell(row=r, column=c, value=dt)
                if isinstance(dt, datetime.datetime):
                    cell.number_format = "YYYY-MM-DD HH:MM:SS"
            else:
                ws.cell(row=r, column=c, value=val)

    wb.save(path)
    return path
