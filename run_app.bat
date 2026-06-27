@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON="

:: 1. py.exe 启动器 - 最可靠的方式
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
    goto :run
)

:: 2. 扫描常见的 Python 安装目录
for /d %%i in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)
for /d %%i in ("C:\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)
for /d %%i in ("D:\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)
for /d %%i in ("E:\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)
for /d %%i in ("F:\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)
for /d %%i in ("G:\Python3*") do (
    if exist "%%i\python.exe" set "PYTHON=%%i\python.exe" & goto :run
)

:: 3. 顺着 pip 找（pip 目录的上一级就是 Python 安装目录）
for /f "delims=" %%i in ('where pip 2^>nul') do (
    if exist "%%~dpi..\python.exe" (
        for %%j in ("%%~dpi..") do set "PYTHON=%%~fj\python.exe"
        goto :run
    )
)

:: 4. 最后尝试 PATH 中的 python（跳过 WindowsApps 假货）
for /f "delims=" %%p in ('where python 2^>nul') do (
    echo %%p | findstr /i "WindowsApps" >nul
    if errorlevel 1 set "PYTHON=%%p" & goto :run
)

:: 没找到
echo [错误] 没找到可用的 Python
echo.
echo 请从 https://www.python.org/downloads/ 下载安装
echo 安装时务必勾选 "Add Python to PATH"
pause
exit /b 1

:run
if not exist "app_launcher.py" (
    echo [错误] 找不到 app_launcher.py，请不要移动 run_app.bat
    pause
    exit /b 1
)

echo 找到 Python: %PYTHON%
echo 正在启动 ZhiJiangAutoClip 工作台...
echo.
"%PYTHON%" app_launcher.py 2>&1
if errorlevel 1 (
    echo.
    echo ========================================
    echo [错误] 启动失败，具体错误见上方
    echo 请截图完整的错误信息（从上到下）
    echo ========================================
    pause
)
