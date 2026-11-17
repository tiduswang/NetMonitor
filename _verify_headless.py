# -*- coding: utf-8 -*-
"""无头验证脚本：采集 -> 落库 -> 筛选 -> 导出，不启动 GUI。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import Collector
from storage import Storage
import exporter

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "verify_test.db")
if os.path.exists(TMP):
    os.remove(TMP)

storage = Storage(db_path=TMP)
collector = Collector(storage, interval=1.0)
collector.start()
print("[1] 采集线程已启动，产生测试流量 (curl)...")
try:
    subprocess.Popen(["curl", "-s", "-o", "NUL", "https://www.baidu.com"],
                     creationflags=subprocess.CREATE_NO_WINDOW)
except Exception as e:
    print("curl 启动失败（忽略）:", e)

time.sleep(20)   # 等待归属地批量解析（首批约 4s 后回写）
stats = collector.stats()
print("[2] 采集统计:", stats)
assert stats["active_conns"] > 0, "未采集到任何连接！"
assert stats["backend"] in ("native", "psutil")

total = storage.total_count()
print("[3] 累计记录数:", total)
assert total > 0

rows = storage.query(limit=10)
print("[4] 样例记录（前 5 条非 LISTEN）:")
shown = 0
for r in rows:
    if r["status"] == "LISTEN":
        continue
    print("    ", {k: r[k] for k in ("process_name", "pid", "remote_ip",
          "remote_port", "protocol", "status", "dns_name", "seen_count")})
    shown += 1
    if shown >= 5:
        break

# 筛选测试
est = storage.query(protocol="TCP", limit=5)
print("[5] 协议筛选 TCP ->", len(est), "条(取5)")
kw = storage.query(keyword=rows[0]["process_name"][:4], limit=5)
print("[6] 关键字筛选 '%s' ->" % rows[0]["process_name"][:4], len(kw), "条(取5)")

# 归属地测试
rows = storage.query(limit=2000)
with_region = [r for r in rows if r.get("ip_region")]
print("[7a] 含归属地记录数:", len(with_region))
for r in with_region[:5]:
    print("     ", r["remote_ip"], "->", r["ip_region"])
assert with_region, "归属地解析全部为空！"
if with_region:
    kw = with_region[0]["ip_region"].split()[0]   # 用国家名检索
    hit = storage.query(keyword=kw, limit=10)
    print("[7b] 按归属地关键字 '%s' 检索 -> %d 条(取10)" % (kw, len(hit)))
    assert hit

# 空数据场景：不可能匹配的筛选
empty = storage.query(ip="203.0.113.255", keyword="nonexistent_proc_xyz")
print("[8] 空数据场景筛选结果:", len(empty), "条（应为 0，无异常）")
assert empty == []

# 导出测试
out = os.path.join(os.path.dirname(TMP), "verify_export.xlsx")
exporter.export_xlsx(storage.query_export(), out)
from openpyxl import load_workbook
wb = load_workbook(out)
ws = wb.active
print("[9] 导出 xlsx:", out)
print("    行数(含表头):", ws.max_row, " 列数:", ws.max_column,
      " 冻结:", ws.freeze_panes)
print("    表头:", [c.value for c in ws[1]][:7], "...")
print("    第2行前7列:", [c.value for c in ws[2]][:7])
assert ws.max_row == storage.total_count() + 1
assert ws.max_column == 13
assert ws.freeze_panes == "A2"

collector.stop()
collector.join(timeout=3)
storage.close()
print("[OK] 全部无头验证通过")
