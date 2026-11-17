# NetMonitor - 本机应用网络连接监测工具

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![OS](https://img.shields.io/badge/os-Windows-0078D6.svg)](https://www.microsoft.com/windows)
[![PySide6](https://img.shields.io/badge/UI-PySide6-green.svg)](https://doc.qt.io/qtforpython/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> 文档分工：[项目说明.md](项目说明.md)（中文项目概览，3 分钟了解全貌）｜[使用文档.md](使用文档.md)（用户操作手册）｜本文件（开发者技术方案与工程细节）。

Windows 桌面软件，实时监测并记录**本机每个应用（进程）访问了哪些 IP 地址**，支持筛选、搜索与 Excel 导出。

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/tiduswang/NetMonitor.git
cd NetMonitor

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行（建议以管理员身份）
python main.py
```

或直接在 [GitHub Releases](https://github.com/tiduswang/NetMonitor/releases) 下载打包好的单文件 `NetMonitor.exe` 双击运行。

## 发布新版本（维护者）

1. 确认代码并提交：`git commit -m "chore: release v0.x.x" && git push`
2. 本地打包：`pyinstaller --noconfirm --onefile --windowed --name NetMonitor main.py`
3. GitHub 创建 Release：
   ```bash
   gh release create v0.x.x dist/NetMonitor.exe ^
     --title "NetMonitor v0.x.x" ^
     --notes "Release notes here"
   ```
4. 首次使用需登录：`gh auth login`，或使用 PAT：`$env:GH_TOKEN="ghp_xxx"; gh auth login --with-token`

## 仓库结构

```
NetMonitor/
├── main.py                    # 程序入口（QApplication 初始化）
├── collector.py               # 采集层：ctypes + psutil 兜底 + DNS + 归属地解析
├── storage.py                 # SQLite 存储：UPSERT 去重、组合筛选、自动清理
├── gui.py                     # PySide6 主窗口、托盘、筛选栏、表格
├── exporter.py                # openpyxl Excel 导出
├── requirements.txt
├── NetMonitor.spec            # PyInstaller 打包配置（建议入库保留）
├── 安装开机自启.bat            # 创建任务计划（需管理员）
├── 卸载开机自启.bat
├── _verify_headless.py        # 无头采集验证脚本（开发辅助）
├── _verify_tray.py            # 托盘行为验证脚本（开发辅助）
├── .gitignore
├── README.md                  # 本文件（开发者视角）
├── 项目说明.md                 # 中文项目概览（面向浏览仓库的访客）
├── 使用文档.md                 # 面向最终用户的完整操作手册
└── data/                      # 运行时 SQLite 数据库目录（已在 .gitignore 中）
```

## .gitignore 说明

以下内容严禁入库：

| 模式 | 说明 |
|---|---|
| `__pycache__/`、`*.py[cod]` | Python 字节码 |
| `build/`、`dist/`、`*.egg-info/` | PyInstaller 和 pip 构建产物 |
| `data/*.db*`、`dist/data/` | 用户运行时生成的 SQLite 数据库 |
| `data/*.xlsx`、`dist/*.xlsx` | 用户导出的 Excel 报表 |
| `*.log`、`run_log.txt` | 运行日志 |
| `.vscode/`、`.idea/` | 编辑器配置 |
| `*.exe`、`*.zip` | 打包好的二进制（通过 GitHub Release 分发） |
| `.env`、`.env.*` | 环境变量/密钥 |

## 功能

- **实时采集**：后台线程每 1 秒快照本机 TCP/UDP 连接表，记录到本地 SQLite。
  - 字段：首次发现时间、最后活跃时间、出现次数、进程名、PID、进程完整路径（需权限）、远程 IP、远程端口、**IP 归属地**（国家/省/市/运营商，中文）、协议、连接状态、本地端口、DNS 域名（PTR 反查，带缓存，失败留空）。
  - **去重口径**：以 `(PID, 远程IP, 远程端口, 协议)` 为唯一键合并。同一条持久连接只保留一行，刷新 `last_seen` 并累加 `seen_count`（约等于活跃秒数），状态变化只更新不新增——不会每秒刷屏。
- **图形界面**（PySide6）：
  - 主表格实时刷新（1s），支持按列排序、暂停/继续采集。
  - 顶部统计：当前活跃应用数、活跃连接数、累计记录数、采集状态与采集后端。
  - 组合筛选：进程名 / 远程 IP / 端口（本地或远程）/ 协议 / 时间范围 / 关键字（进程、IP、域名、路径模糊匹配），输入即时生效。
  - 清空数据库（二次确认）。
  - **托盘常驻**：点击窗口关闭按钮不退出程序，而是隐藏到系统托盘继续采集；双击托盘图标恢复窗口；只有托盘右键菜单"退出"才真正结束进程（`app.setQuitOnLastWindowClosed(False)` + `closeEvent` 拦截）。
- **导出 Excel**：导出当前筛选结果或全部记录为 `.xlsx`，含表头样式、时间列格式化、合理列宽、冻结首行；导出后可一键打开。

## 技术方案与取舍

| 项 | 方案 | 说明 |
|---|---|---|
| 连接采集（首选） | `ctypes` 调用 IPHLPAPI `GetExtendedTcpTable` / `GetExtendedUdpTable`（IPv4+IPv6，OWNER_PID 表） | Windows 原生、低开销、直接带 PID |
| 连接采集（兜底） | `psutil.net_connections(kind='inet')` | 原生调用失败时自动降级，跨平台 |
| 进程映射 | `psutil.Process(pid).name()/exe()` + PID 缓存 | 路径需要权限，失败时留空只记进程名 |
| DNS 反查 | 独立后台线程 `socket.gethostbyaddr`，内存缓存 | 不阻塞采集主循环；反查成功后回写数据库 |
| IP 归属地 | 独立后台线程调 ip-api.com 批量接口（`lang=zh-CN`，50 IP/批，≥2s/批，远低于 45 次/分限额），内存缓存 | 只查公网 IP（内网/回环自动跳过）；结果形如"中国 江苏 南京 电信"；无网络时留空、不影响采集 |
| 存储 | SQLite（WAL 模式），`data/net_monitor.db` | 长期运行：保留 30 天 + 最多 20 万条，每小时自动清理（见 `storage.py` 顶部常量） |
| GUI | PySide6 | 表格排序/高频刷新性能好，打包成熟 |
| ETW | 未采用 | Microsoft-Windows-TCPIP ETW 能做到事件级精度，但需要管理员 + 复杂的事件解析；连接表轮询（1s）已满足“记录每个应用访问了哪些 IP”的需求，开销更低、实现更稳 |

**安全性**：程序只做只读查询（连接表、进程信息、DNS 反查），不注入进程、不 hook、不修改系统网络配置，不读取任何报文内容，只看连接元数据。

**异常自恢复**：采集线程主循环整体 try/except，单次采集失败仅累计错误计数并继续；原生 API 失败自动切换到 psutil 后端。

## 安装与启动

```powershell
cd net_monitor
pip install -r requirements.txt
python main.py
```

> **建议以管理员身份运行**：非管理员下，其他用户/系统进程的路径与部分连接映射会拿不到（界面上会显示 `PID:xxx`，进程路径为空）。程序启动时会在状态栏给出权限提示。

## 打包 exe（PyInstaller）

```powershell
cd net_monitor
pyinstaller --noconfirm --onefile --windowed --name NetMonitor main.py
```

产物：`dist\NetMonitor.exe`（单文件、无控制台窗口）。数据库会自动创建在 exe 同级的 `data\` 目录下。

## 项目结构

```
net_monitor/
├── main.py          # 入口：启动采集线程 + GUI
├── collector.py     # 采集层：ctypes IPHLPAPI / psutil 兜底、进程缓存、DNS 反查
├── storage.py       # SQLite 存储：去重合并、筛选查询、自动清理
├── exporter.py      # Excel 导出（openpyxl）
├── gui.py           # PySide6 界面
├── requirements.txt
├── README.md
└── data/            # 运行时生成：net_monitor.db
```

## 已知限制

1. **UDP 无远端信息**：`GetExtendedUdpTable` 与 psutil 均只能拿到 UDP 套接字的本地地址/端口（UDP 无连接概念），因此 UDP 记录的远程 IP/端口为空。
2. **短连接可能漏采**：1 秒轮询可能错过生命周期 <1s 的短连接（首次发现≠建立时刻）；如需事件级精度可扩展 ETW。
3. **DNS 反查覆盖有限**：很多 IP 无 PTR 记录或反查得到 CDN 节点名，域名列为尽力而为，失败留空。
4. **归属地依赖在线接口**：ip-api.com 免费版精度到市级，偶有偏差；无网络时该列留空（其余功能不受影响）。如需离线/高精度，可替换为 ip2region 本地库。
5. **权限**：非管理员运行时，部分进程的名称/路径不可见。
6. **PID 复用**：以 PID+四元组去重，极端情况下 PID 快速复用可能把两个连接合并为一条（概率极低）。

## 后续可扩展点

- ETW（Microsoft-Windows-Kernel-Network）事件级采集，捕获短连接；
- 按进程聚合的流量统计（结合 `GetPerTcpConnectionEstats` 或 psutil io_counters）；
- IP 地理位置 / 威胁情报标注；
- 远程 IP 画像与告警规则（如命中黑名单弹窗提醒）。
