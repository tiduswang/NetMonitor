# -*- coding: utf-8 -*-
"""SQLite 存储层。

去重口径：以 (PID, 远程IP, 远程端口, 协议) 为唯一键合并。
同一条持久连接只保留一行：
  - first_seen  首次发现时间
  - last_seen   最后活跃时间（每次快照仍存在则刷新）
  - seen_count  被快照命中次数（约等于活跃秒数，默认 1s/次）
  - status      最新 TCP 状态（状态变化只更新，不新增行）

长期运行策略：启动时与每小时执行一次清理——
  1) 删除 last_seen 早于 RETENTION_DAYS 天的记录；
  2) 若总行数超过 MAX_ROWS，按 last_seen 从最旧开始裁剪。
"""
import os
import sqlite3
import threading
import time

RETENTION_DAYS = 30        # 保留最近 30 天
MAX_ROWS = 200_000         # 最多保留 20 万条
CLEANUP_INTERVAL = 3600    # 每小时检查一次

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key    TEXT UNIQUE NOT NULL,
    process_name TEXT,
    pid          INTEGER,
    process_path TEXT,
    remote_ip    TEXT,
    remote_port  INTEGER,
    protocol     TEXT,
    status       TEXT,
    local_port   INTEGER,
    dns_name     TEXT,
    ip_region    TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    seen_count   INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_last_seen ON connections(last_seen);
CREATE INDEX IF NOT EXISTS idx_proc ON connections(process_name);
CREATE INDEX IF NOT EXISTS idx_ip   ON connections(remote_ip);
"""


def default_db_path():
    """数据库位置：程序同级 data 目录（exe 打包后取 exe 所在目录）。"""
    import sys
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "net_monitor.db")


class Storage:
    def __init__(self, db_path=None):
        self.db_path = db_path or default_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            # 老库迁移：补充后加的列
            cols = {r[1] for r in self._conn.execute(
                "PRAGMA table_info(connections)").fetchall()}
            if "ip_region" not in cols:
                self._conn.execute(
                    "ALTER TABLE connections ADD COLUMN ip_region TEXT")
            self._conn.commit()
        self._last_cleanup = 0
        self.cleanup()

    # ------------------------------------------------------------ 写入
    def upsert_batch(self, batch):
        """批量合并写入一个快照。"""
        if not batch:
            return
        with self._lock:
            cur = self._conn.cursor()
            for c in batch:
                key = "%d|%s|%d|%s" % (c["pid"], c["remote_ip"],
                                       c["remote_port"], c["protocol"])
                cur.execute(
                    """INSERT INTO connections
                       (dedup_key, process_name, pid, process_path, remote_ip,
                        remote_port, protocol, status, local_port, dns_name,
                        ip_region, first_seen, last_seen, seen_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                       ON CONFLICT(dedup_key) DO UPDATE SET
                           last_seen   = excluded.last_seen,
                           seen_count  = seen_count + 1,
                           status      = excluded.status,
                           process_name= excluded.process_name,
                           process_path= CASE WHEN excluded.process_path != ''
                                              THEN excluded.process_path
                                              ELSE process_path END,
                           dns_name    = CASE WHEN excluded.dns_name != ''
                                              THEN excluded.dns_name
                                              ELSE dns_name END,
                           ip_region   = CASE WHEN excluded.ip_region != ''
                                              THEN excluded.ip_region
                                              ELSE ip_region END
                    """,
                    (key, c["process_name"], c["pid"], c["process_path"],
                     c["remote_ip"], c["remote_port"], c["protocol"],
                     c["status"], c["local_port"], c["dns_name"],
                     c.get("ip_region", ""), c["now"], c["now"]))
            self._conn.commit()
        self._maybe_cleanup()

    def update_dns(self, ip, hostname):
        with self._lock:
            self._conn.execute(
                "UPDATE connections SET dns_name=? WHERE remote_ip=? "
                "AND (dns_name IS NULL OR dns_name='')", (hostname, ip))
            self._conn.commit()

    def update_region(self, ip, region):
        with self._lock:
            self._conn.execute(
                "UPDATE connections SET ip_region=? WHERE remote_ip=? "
                "AND (ip_region IS NULL OR ip_region='')", (region, ip))
            self._conn.commit()

    # ------------------------------------------------------------ 查询
    def query(self, process="", ip="", port="", protocol="",
              time_from="", time_to="", keyword="",
              limit=2000, offset=0):
        """组合筛选查询，所有条件 AND；keyword 跨多列模糊匹配。"""
        sql = ("SELECT process_name, pid, remote_ip, remote_port, protocol,"
               " status, local_port, dns_name, ip_region, first_seen, last_seen,"
               " seen_count, process_path FROM connections WHERE 1=1")
        args = []
        if process:
            sql += " AND process_name LIKE ?"
            args.append("%" + process + "%")
        if ip:
            sql += " AND remote_ip LIKE ?"
            args.append("%" + ip + "%")
        if port:
            sql += " AND (remote_port = ? OR local_port = ?)"
            try:
                p = int(port)
            except ValueError:
                p = -1
            args += [p, p]
        if protocol and protocol != "全部":
            sql += " AND protocol = ?"
            args.append(protocol)
        if time_from:
            sql += " AND last_seen >= ?"
            args.append(time_from)
        if time_to:
            sql += " AND last_seen <= ?"
            args.append(time_to)
        if keyword:
            sql += (" AND (process_name LIKE ? OR remote_ip LIKE ?"
                    " OR dns_name LIKE ? OR process_path LIKE ?"
                    " OR ip_region LIKE ?)")
            kw = "%" + keyword + "%"
            args += [kw, kw, kw, kw, kw]
        sql += " ORDER BY last_seen DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def query_export(self, **filters):
        """导出用：不走 LIMIT，取全量匹配记录。"""
        filters["limit"] = 1_000_000
        return self.query(**filters)

    def total_count(self):
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM connections").fetchone()[0]

    def clear(self):
        with self._lock:
            self._conn.execute("DELETE FROM connections")
            self._conn.commit()

    # ------------------------------------------------------------ 清理
    def _maybe_cleanup(self):
        if time.time() - self._last_cleanup > CLEANUP_INTERVAL:
            self.cleanup()

    def cleanup(self):
        self._last_cleanup = time.time()
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(time.time() - RETENTION_DAYS * 86400))
        with self._lock:
            self._conn.execute(
                "DELETE FROM connections WHERE last_seen < ?", (cutoff,))
            self._conn.execute(
                """DELETE FROM connections WHERE id NOT IN (
                       SELECT id FROM connections
                       ORDER BY last_seen DESC LIMIT ?)""", (MAX_ROWS,))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
