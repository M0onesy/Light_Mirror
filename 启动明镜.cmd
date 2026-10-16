@echo off
chcp 936 >nul
rem ============================================================
rem  明镜 MingJing 本地服务启动器（常驻，不依赖任何工具进程）
rem  放置位置：Light_Mirror 仓库根目录（与本文件同级应有 mingjing 文件夹）
rem  双击本文件即可启动；关闭本窗口即停止明镜服务。
rem ============================================================
setlocal enableextensions

rem 1) 切到本脚本所在目录（即仓库根）
cd /d "%~dp0"

rem 2) 系统 Python 3.12 绝对路径（不依赖任何托管运行时）
set "MINGJING_PY=C:/Users/xy/AppData/Local/Programs/Python/Python312/python.exe"

if not exist "%MINGJING_PY%" (
    echo.
    echo [错误] 未找到系统 Python：%MINGJING_PY%
    echo        请确认已安装 Python 3.12，或修改本脚本中的 MINGJING_PY 路径。
    echo.
    pause
    exit /b 1
)

if not exist "mingjing\launcher.py" (
    echo.
    echo [错误] 当前目录不是 MingJing 仓库根目录：%CD%
    echo        请把本脚本放到 Light_Mirror 仓库根目录后再双击。
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo   明镜 MingJing 正在启动...
echo   工作目录 : %CD%
echo   数据目录 : %LOCALAPPDATA%\MingJing   （平台默认，未改动）
echo   ------------------------------------------------------------
echo   浏览器会自动打开。请保持本窗口不要关闭；
echo   关闭本窗口或按 Ctrl+C 即停止明镜服务。
echo ============================================================
echo.

rem 3) 用系统 Python 启动本地服务（平台默认会自动打开浏览器）
"%MINGJING_PY%" -m mingjing.launcher

set "RC=%ERRORLEVEL%"
echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
    echo 明镜服务已正常退出。
) else (
    echo 明镜服务异常退出，返回码=%RC%。
    echo 可查看日志：%LOCALAPPDATA%\MingJing\logs\launcher.log
)
echo ------------------------------------------------------------
pause
endlocal
