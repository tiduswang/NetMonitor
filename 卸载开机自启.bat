@echo off
:: 卸载 NetMonitor 开机自启
:: 使用方法：右键本文件 -> “以管理员身份运行”

schtasks /Delete /TN "NetMonitor" /F
if %errorlevel%==0 (
    echo [成功] 已删除任务计划 "NetMonitor"，开机自启已取消。
) else (
    echo [提示] 删除失败或任务不存在；如提示权限不足，请以管理员身份运行本脚本。
)
pause
