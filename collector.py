# -*- coding: utf-8 -*-
"""网络连接采集层。

首选方案：ctypes 调用 Windows IPHLPAPI 的
GetExtendedTcpTable / GetExtendedUdpTable（含 IPv4/IPv6），
直接拿到 连接五元组 + 所属 PID，开销低、不依赖第三方驱动。
兜底方案：psutil.net_connections(kind='inet')（跨平台）。

只做只读查询，不注入、不 hook、不修改系统配置，不读取任何报文内容。
"""
import ctypes
import ipaddress
import json
import socket
import struct
import threading
import time
import urllib.request
from ctypes import wintypes

import psutil

# ---------------------------------------------------------------- TCP 状态表
TCP_STATES = {
    1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD",
    5: "ESTABLISHED", 6: "FIN_WAIT1", 7: "FIN_WAIT2", 8: "CLOSE_WAIT",
    9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB",
}

AF_INET = 2
AF_INET6 = 23
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1

iphlpapi = ctypes.windll.iphlpapi if hasattr(ctypes, "windll") else None


# ---------------------------------------------------------------- 行结构体
class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD),
                ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16),
                ("dwLocalScopeId", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("ucRemoteAddr", ctypes.c_ubyte * 16),
                ("dwRemoteScopeId", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwState", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


class MIB_UDPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


class MIB_UDP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16),
                ("dwLocalScopeId", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


def _ipv4(dw):
    return socket.inet_ntoa(struct.pack("<L", dw))


def _ipv6(bts):
    return socket.inet_ntop(socket.AF_INET6, bytes(bts))


def _query_table(func, af, table_class, row_struct):
    """通用的 GetExtended*Table 调用：先取长度，再分配缓冲区取数据。"""
    size = wintypes.DWORD(0)
    # 第一次调用拿到需要的缓冲区大小
    func(None, ctypes.byref(size), False, af, table_class, 0)
    buf = (ctypes.c_ubyte * size.value)()
    if func(buf, ctypes.byref(size), False, af, table_class, 0) != 0:
        return []
    num = struct.unpack("<L", bytes(buf[0:4]))[0]
    row_size = ctypes.sizeof(row_struct)
    rows = []
    for i in range(num):
        row = row_struct.from_buffer_copy(bytes(buf[4 + i * row_size: 4 + (i + 1) * row_size]))
        rows.append(row)
    return rows


def _collect_native():
    """ctypes + IPHLPAPI 采集，返回连接 dict 列表。"""
    conns = []

    # ---- TCP IPv4 / IPv6
    for af, row_struct, is6 in ((AF_INET, MIB_TCPROW_OWNER_PID, False),
                                (AF_INET6, MIB_TCP6ROW_OWNER_PID, True)):
        try:
            rows = _query_table(iphlpapi.GetExtendedTcpTable, af,
                                TCP_TABLE_OWNER_PID_ALL, row_struct)
        except Exception:
            continue
        for r in rows:
            try:
                if is6:
                    lip, rip = _ipv6(r.ucLocalAddr), _ipv6(r.ucRemoteAddr)
                else:
                    lip, rip = _ipv4(r.dwLocalAddr), _ipv4(r.dwRemoteAddr)
                conns.append({
                    "protocol": "TCP",
                    "local_ip": lip,
                    "local_port": socket.ntohs(r.dwLocalPort),
                    "remote_ip": rip,
                    "remote_port": socket.ntohs(r.dwRemotePort),
                    "status": TCP_STATES.get(r.dwState, str(r.dwState)),
                    "pid": r.dwOwningPid,
                })
            except Exception:
                continue

    # ---- UDP IPv4 / IPv6（UDP 表不含远端信息，属于协议本身限制）
    for af, row_struct, is6 in ((AF_INET, MIB_UDPROW_OWNER_PID, False),
                                (AF_INET6, MIB_UDP6ROW_OWNER_PID, True)):
        try:
            rows = _query_table(iphlpapi.GetExtendedUdpTable, af,
                                UDP_TABLE_OWNER_PID, row_struct)
        except Exception:
            continue
        for r in rows:
            try:
                lip = _ipv6(r.ucLocalAddr) if is6 else _ipv4(r.dwLocalAddr)
                conns.append({
                    "protocol": "UDP",
                    "local_ip": lip,
                    "local_port": socket.ntohs(r.dwLocalPort),
                    "remote_ip": "",
                    "remote_port": 0,
                    "status": "NONE",
                    "pid": r.dwOwningPid,
                })
            except Exception:
                continue
    return conns


def _collect_psutil():
    """psutil 兜底采集。"""
    conns = []
    for c in psutil.net_connections(kind="inet"):
        try:
            lip, lport = (c.laddr.ip, c.laddr.port) if c.laddr else ("", 0)
            rip, rport = (c.raddr.ip, c.raddr.port) if c.raddr else ("", 0)
            conns.append({
                "protocol": "TCP" if c.type == socket.SOCK_STREAM else "UDP",
                "local_ip": lip, "local_port": lport,
                "remote_ip": rip, "remote_port": rport,
                "status": c.status or "NONE",
                "pid": c.pid or 0,
            })
        except Exception:
            continue
    return conns


# ---------------------------------------------------------------- 进程信息缓存
class ProcessCache:
    """PID -> (进程名, 完整路径)。路径需要权限，失败时留空。"""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, pid):
        if pid == 0:
            return ("System Idle", "")
        if pid == 4:
            return ("System", "")
        with self._lock:
            hit = self._cache.get(pid)
        if hit:
            return hit
        name, path = ("PID:%d" % pid, "")
        try:
            p = psutil.Process(pid)
            name = p.name()
            try:
                path = p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                path = ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
        with self._lock:
            self._cache[pid] = (name, path)
        return (name, path)

    def prune(self, alive_pids):
        with self._lock:
            for pid in list(self._cache):
                if pid not in alive_pids:
                    del self._cache[pid]


# ---------------------------------------------------------------- DNS 反查（后台线程 + 缓存）
class DnsResolver(threading.Thread):
    """对远程 IP 做 PTR 反查，结果缓存；失败留空。解析到后回调写库。"""

    def __init__(self, on_resolved):
        super().__init__(daemon=True, name="DnsResolver")
        self._queue = []
        self._cond = threading.Condition()
        self._cache = {}          # ip -> hostname or ""
        self._pending = set()
        self._on_resolved = on_resolved
        self._stop = False

    def resolve_async(self, ip):
        if not ip or ip.startswith("127.") or ip == "::1" \
                or ip in ("0.0.0.0", "::") or ip in self._cache:
            return
        with self._cond:
            if ip in self._pending:
                return
            self._pending.add(ip)
            self._queue.append(ip)
            self._cond.notify()

    def get(self, ip):
        return self._cache.get(ip, "")

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()

    def run(self):
        socket.setdefaulttimeout(2.0)
        while True:
            with self._cond:
                while not self._queue and not self._stop:
                    self._cond.wait(timeout=1.0)
                if self._stop:
                    return
                ip = self._queue.pop(0)
                self._pending.discard(ip)
            name = ""
            try:
                name = socket.gethostbyaddr(ip)[0]
            except Exception:
                name = ""
            self._cache[ip] = name
            if name:
                try:
                    self._on_resolved(ip, name)
                except Exception:
                    pass


# ---------------------------------------------------------------- 归属地工具
def is_public_ip(ip):
    """只查询公网 IP 的归属地；内网/回环/保留地址直接跳过。"""
    try:
        a = ipaddress.ip_address(ip)
        return not (a.is_private or a.is_loopback or a.is_reserved
                    or a.is_link_local or a.is_multicast or a.is_unspecified)
    except ValueError:
        return False


class RegionResolver(threading.Thread):
    """IP 归属地解析：ip-api.com 批量接口（中文结果），内存缓存 + 限速。

    免费版限制 45 次/分钟，批量接口一次最多 100 个 IP，
    这里每批最多 50 个、批次间隔 >=2s，远低于限额。
    解析成功/失败都缓存，失败留空；不阻塞采集主循环。
    """

    API = ("http://ip-api.com/batch?lang=zh-CN"
           "&fields=status,query,country,regionName,city,isp")
    BATCH_SIZE = 50
    BATCH_INTERVAL = 2.0

    def __init__(self, on_resolved):
        super().__init__(daemon=True, name="RegionResolver")
        self._cond = threading.Condition()
        self._queue = []
        self._pending = set()
        self._cache = {}          # ip -> region or ""
        self._on_resolved = on_resolved
        self._stop = False

    def resolve_async(self, ip):
        if not ip or not is_public_ip(ip) or ip in self._cache:
            return
        with self._cond:
            if ip in self._pending:
                return
            self._pending.add(ip)
            self._queue.append(ip)
            self._cond.notify()

    def get(self, ip):
        return self._cache.get(ip, "")

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()

    @staticmethod
    def _query_batch(ips):
        """返回 {ip: region}；网络异常时返回 None（由调用方重排队）。"""
        body = json.dumps(ips).encode("utf-8")
        req = urllib.request.Request(RegionResolver.API, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = {}
        for item in data:
            if item.get("status") == "success":
                parts = [item.get("country", ""), item.get("regionName", ""),
                         item.get("city", ""), item.get("isp", "")]
                out[item["query"]] = " ".join(p for p in parts if p)
            else:
                out[item.get("query", "")] = ""
        return out

    def run(self):
        while True:
            with self._cond:
                # 等待凑批：有量或超时 2s 取一次
                while not self._queue and not self._stop:
                    self._cond.wait(timeout=self.BATCH_INTERVAL)
                if self._stop:
                    return
                if len(self._queue) < self.BATCH_SIZE:
                    self._cond.wait(timeout=self.BATCH_INTERVAL)
                    if self._stop:
                        return
                batch = self._queue[:self.BATCH_SIZE]
                del self._queue[:self.BATCH_SIZE]
                for ip in batch:
                    self._pending.discard(ip)
            try:
                result = self._query_batch(batch)
            except Exception:
                result = None
            if result is None:
                # 网络故障：写空串避免反复重试（下次重启程序后可再查）
                result = {ip: "" for ip in batch}
            for ip, region in result.items():
                self._cache[ip] = region
                if region:
                    try:
                        self._on_resolved(ip, region)
                    except Exception:
                        pass


# ---------------------------------------------------------------- 采集线程
class Collector(threading.Thread):
    """后台采集线程：周期性快照连接表并写入存储层。

    单次采集失败只记录错误并继续，线程永不因异常退出（自恢复）。
    """

    def __init__(self, storage, interval=1.0):
        super().__init__(daemon=True, name="Collector")
        self.storage = storage
        self.interval = interval
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._lock = threading.Lock()
        self._stats = {"active_apps": 0, "active_conns": 0,
                       "running": False, "errors": 0, "backend": ""}
        self.proc_cache = ProcessCache()
        self.resolver = DnsResolver(on_resolved=storage.update_dns)
        self.region_resolver = RegionResolver(on_resolved=storage.update_region)

    # ---- 控制
    def stop(self):
        self._stop_evt.set()

    def pause(self):
        self._pause_evt.set()

    def resume(self):
        self._pause_evt.clear()

    @property
    def paused(self):
        return self._pause_evt.is_set()

    def stats(self):
        with self._lock:
            return dict(self._stats)

    # ---- 主循环
    def run(self):
        self.resolver.start()
        self.region_resolver.start()
        backend = "native"
        while not self._stop_evt.is_set():
            if self._pause_evt.is_set():
                with self._lock:
                    self._stats["running"] = False
                time.sleep(0.3)
                continue
            try:
                if backend == "native" and iphlpapi is not None:
                    try:
                        conns = _collect_native()
                    except Exception:
                        backend = "psutil"      # 原生失败自动降级
                        conns = _collect_psutil()
                else:
                    conns = _collect_psutil()

                now = time.strftime("%Y-%m-%d %H:%M:%S")
                batch = []
                apps = set()
                for c in conns:
                    name, path = self.proc_cache.get(c["pid"])
                    apps.add(name)
                    dns = ""
                    region = ""
                    if c["remote_ip"]:
                        dns = self.resolver.get(c["remote_ip"])
                        if not dns:
                            self.resolver.resolve_async(c["remote_ip"])
                        region = self.region_resolver.get(c["remote_ip"])
                        if not region:
                            self.region_resolver.resolve_async(c["remote_ip"])
                    batch.append({
                        "process_name": name,
                        "pid": c["pid"],
                        "process_path": path,
                        "remote_ip": c["remote_ip"],
                        "remote_port": c["remote_port"],
                        "protocol": c["protocol"],
                        "status": c["status"],
                        "local_port": c["local_port"],
                        "dns_name": dns,
                        "ip_region": region,
                        "now": now,
                    })
                self.storage.upsert_batch(batch)
                self.proc_cache.prune({c["pid"] for c in conns})
                with self._lock:
                    self._stats.update({
                        "active_apps": len(apps),
                        "active_conns": len(conns),
                        "running": True,
                        "backend": backend,
                    })
            except Exception:
                with self._lock:
                    self._stats["errors"] += 1
            self._stop_evt.wait(self.interval)
        self.resolver.stop()
        self.region_resolver.stop()
