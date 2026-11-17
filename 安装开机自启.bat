@echo off
:: 安装 NetMonitor 开机自启（管理员权限，登录时自动运行）
:: 使用方法：右键本文件 -> “以管理员身份运行”

set "EXE=%~dp0dist\NetMonitor.exe"
if not exist "%EXE%" (
    echo [错误] 未找到 %EXE%
    echo 请先执行 PyInstaller 打包，或修改本脚本中的 EXE 路径。
    pause
    exit /b 1
)

:: 通过任务计划程序实现“登录时以最高权限启动”，不会产生 UAC 弹窗
schtasks /Create /TN "NetMonitor" /TR "\"%EXE%\"" /SC ONLOGON /RL HIGHEST /F
if %errorlevel%==0 (
    echo.
    echo [成功] 已创建任务计划 "NetMonitor"：
    echo   - 触发器：当前用户登录时
    echo   - 权限：最高权限（管理员）
    echo   - 程序：%EXE%
    echo.
    echo 下次登录 Windows 时将自动启动；现在可执行  schtasks /Run /TN "NetMonitor"  立即启动一次。
    echo 卸载：以管理员身份运行 卸载开机自启.bat
) else (
    echo [失败] 请确认本脚本是以管理员身份运行的。
)
pause
