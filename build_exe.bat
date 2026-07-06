@echo off
chcp 65001 >nul
echo ========================================
echo  启小铺订单自动化 — EXE 构建脚本
echo ========================================

cd /d "%~dp0"

REM 清理旧构建
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

echo.
echo [1/2] 构建 EXE...
python -m PyInstaller build.spec

if %ERRORLEVEL% neq 0 (
    echo.
    echo ❌ 构建失败！
    pause
    exit /b 1
)

echo.
echo [2/2] 清理中间文件...
if exist "build" rmdir /s /q "build"

echo.
echo ✅ 构建成功！
echo    输出: dist\启小铺订单自动化.exe
echo.
echo 文件大小:
dir "dist\启小铺订单自动化.exe" 2>nul
echo.

pause
