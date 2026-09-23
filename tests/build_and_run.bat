@echo off
REM Builds and runs the pure C++ packet tests (no game, no YYToolkit needed).
setlocal enabledelayedexpansion
if defined VSCMD_VER (
    where cl >nul 2>nul
    if not errorlevel 1 goto :have_vs
)
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :nope
for /f "usebackq tokens=* delims=" %%i in (`""!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath"`) do (
    set "VSWHERE_PATH=%%i"
)
if not defined VSWHERE_PATH goto :nope
if not exist "%VSWHERE_PATH%\VC\Auxiliary\Build\vcvars64.bat" goto :nope
call "%VSWHERE_PATH%\VC\Auxiliary\Build\vcvars64.bat" >nul
goto :have_vs
:nope
echo ERROR: Visual Studio C++ tools not found
exit /b 1

:have_vs
cd /d "%~dp0"
if not exist obj mkdir obj
cl /nologo /std:c++20 /EHsc /W4 /permissive- /I "%~dp0..\plugin\include" cpp\packet_smoke.cpp /Fe:obj\packet_smoke.exe /Fo:obj\
if errorlevel 1 ( echo TEST BUILD FAILED & exit /b 1 )
obj\packet_smoke.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++20 /EHsc /W4 /permissive- /I "%~dp0..\plugin\include" cpp\runtime_smoke.cpp /Fe:obj\runtime_smoke.exe /Fo:obj\
if errorlevel 1 exit /b 1
obj\runtime_smoke.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++20 /EHsc /W4 /permissive- /I "%~dp0..\plugin\include" /I "%~dp0..\..\hs-game-sdk\cpp\include" cpp\rewards_smoke.cpp /Fe:obj\rewards_smoke.exe /Fo:obj\
if errorlevel 1 exit /b 1
obj\rewards_smoke.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++20 /EHsc /W4 /permissive- /I "%~dp0..\plugin\include" cpp\worker_smoke.cpp /Fe:obj\worker_smoke.exe /Fo:obj\
if errorlevel 1 exit /b 1
obj\worker_smoke.exe
if errorlevel 1 exit /b 1
exit /b 0
