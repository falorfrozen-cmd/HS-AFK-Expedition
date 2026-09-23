@echo off
REM HS AFK Expedition plugin compiler (modelled on ForgePact's build.bat).
REM   build.bat          -> HSAfkExpeditionPlugin.dll
REM   build.bat deploy   -> build, then copy the DLL into the game's mods\aurie
REM                         (needs HS_GAME_BIN=<path to HeroSiege\bin>)
setlocal enabledelayedexpansion
if defined VSCMD_VER (
    where cl >nul 2>nul
    if not errorlevel 1 (
        echo using already-initialised MSVC environment
        goto :have_vs
    )
)
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :nope
REM Delayed expansion: the "(x86)" in the path must not be expanded while cmd
REM parses the for-loop parentheses.
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
if not exist "include\YYToolkit\YYTK_Shared.hpp" (
    echo YYToolkit headers missing under plugin_build\include - running tools\prepare_toolchain.py
    py "%~dp0..\tools\prepare_toolchain.py"
    if errorlevel 1 ( echo ERROR: toolchain headers not available & exit /b 1 )
)
if not exist obj mkdir obj
REM YYTK_DEFINE_INTERNAL must be on the whole command line (see ForgePact/plugin/BUILD.md).
cl /nologo /std:c++20 /EHsc /MD /LD /O2 /DNDEBUG /DYYTK_DEFINE_INTERNAL=1 /I "include" /I "%~dp0..\plugin\include" /I "%~dp0..\..\hs-game-sdk\cpp\include" "%~dp0..\plugin\ModuleMain.cpp" "include\YYToolkit\YYTK_Shared_Types.cpp" /Fe:HSAfkExpeditionPlugin.dll /Fo:obj\ /link /DLL user32.lib
if errorlevel 1 ( echo BUILD FAILED & exit /b 1 )
echo DONE -^> plugin_build\HSAfkExpeditionPlugin.dll
if /I "%~1"=="deploy" (
    if not defined HS_GAME_BIN ( echo ERROR: set HS_GAME_BIN to the game's bin folder & exit /b 1 )
    copy /y HSAfkExpeditionPlugin.dll "%HS_GAME_BIN%\mods\aurie\HSAfkExpeditionPlugin.dll"
    if errorlevel 1 ( echo ERROR: deploy copy failed & exit /b 1 )
    echo deployed to "!HS_GAME_BIN!\mods\aurie"
)
