@echo off
chcp 936 >nul
rem ============================================================
rem  明镜 MingJing 本地服务启动器
rem  放置位置：Light_Mirror 仓库根目录（与本文件同级应有 mingjing 文件夹）
rem  双击本文件即可启动；关闭本窗口即停止明镜服务。
rem  脚本会自动查找 Python 并检查运行依赖，无需手工修改路径。
rem ============================================================
setlocal enableextensions

rem 1) 切到本脚本所在目录（即仓库根）
cd /d "%~dp0"

rem 2) 依次尝试查找可用的 Python：
rem    先沿用打包机器上的已知路径，再退回 py 启动器、PATH、常见安装目录
set "MINGJING_PY="
if exist "C:\Users\xy\AppData\Local\Programs\Python\Python312\python.exe" set "MINGJING_PY=C:\Users\xy\AppData\Local\Programs\Python\Python312\python.exe"
if not defined MINGJING_PY for /f "usebackq delims=" %%I in (`py -3.12 -c "import sys;print(sys.executable)" 2^>nul`) do set "MINGJING_PY=%%I"
if not defined MINGJING_PY for /f "usebackq delims=" %%I in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do set "MINGJING_PY=%%I"
if not defined MINGJING_PY for /f "usebackq delims=" %%I in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "MINGJING_PY=%%I"
if not defined MINGJING_PY for %%D in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%ProgramFiles%\Python312\python.exe" "%ProgramFiles%\Python313\python.exe" "C:\Python312\python.exe" "C:\Python313\python.exe") do if not defined MINGJING_PY if exist "%%~D" set "MINGJING_PY=%%~D"

if not defined MINGJING_PY (
    echo.
    echo [错误] 没有找到 Python。本平台需要 Python 3.12 或更高版本。
    echo        下载地址：https://www.python.org/downloads/
    echo        安装时请勾选 "Add python.exe to PATH"，然后重新双击本文件。
    echo.
    pause
    exit /b 1
)

echo 使用的 Python：%MINGJING_PY%
echo.

rem 3) 确认当前目录是仓库根
if not exist "mingjing\launcher.py" (
    echo [错误] 当前目录不是 MingJing 仓库根目录：%CD%
    echo        请把本脚本放在 Light_Mirror 仓库根目录后再双击。
    echo.
    pause
    exit /b 1
)

rem 4) 保持控制台编码一致，避免中文输出在管道/重定向时出现乱码
set "PYTHONIOENCODING=gbk:replace"

rem 5) 检查 Python 版本与运行依赖（缺失时自动安装）
"%MINGJING_PY%" "scripts\ensure_runtime_deps.py"
if errorlevel 1 (
    echo.
    echo [错误] 运行环境检查未通过，已停止启动。
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   明镜 MingJing 正在启动...
echo   工作目录 : %CD%
echo   数据目录 : %LOCALAPPDATA%\MingJing   （平台默认，未改动）
echo   ------------------------------------------------------------
echo   浏览器会自动打开。请保持本窗口不要关闭；
echo   关闭本窗口或按 Ctrl+C 即停止明镜服务。
echo ============================================================
echo.

rem 6) 启动本地服务（平台默认会自动打开浏览器）
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
