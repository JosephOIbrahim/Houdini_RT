@echo off
REM verify/s1_build.bat - MSVC build helper for the s1 exam. Called from s1_furnace.sh.
REM Exit 11: shader compile failed. Exit 12: host compile/link failed. Exit 13: toolchain missing.
REM Arguments: %1 = repo root (Windows path), %2 = openpbr-bsdf root (Windows path)

setlocal
set "ROOT=%~1"
set "OPENPBR=%~2"
if "%ROOT%"=="" exit /b 13
if "%OPENPBR%"=="" exit /b 13
if not exist "%OPENPBR%\openpbr.h" (
  echo s1_build: openpbr.h not found under "%OPENPBR%"
  exit /b 13
)
if "%VULKAN_SDK%"=="" (
  echo s1_build: VULKAN_SDK is not set
  exit /b 13
)

set "OUT=%ROOT%\build\s1"
if not exist "%OUT%" mkdir "%OUT%"

REM --- MSVC environment -----------------------------------------------------
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo s1_build: vswhere.exe not found; is Visual Studio or Build Tools installed?
  exit /b 13
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if "%VSROOT%"=="" (
  echo s1_build: no Visual Studio with the C++ x64 toolset found
  exit /b 13
)
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo s1_build: vcvars64.bat failed
  exit /b 13
)

REM --- shader ----------------------------------------------------------------
where glslangValidator >nul 2>&1
if errorlevel 1 set "PATH=%VULKAN_SDK%\Bin;%PATH%"
echo s1_build: glslangValidator
glslangValidator -V --target-env vulkan1.2 -S comp -DFURNACE_TEX_LUTS -I"%OPENPBR%" "%ROOT%\src\furnace\furnace.comp" -o "%OUT%\furnace_tex.spv"
if errorlevel 1 (
  echo s1_build: shader compile failed
  exit /b 11
)

REM --- host ------------------------------------------------------------------
echo s1_build: cl furnace_host.cpp
pushd "%OUT%"
cl /nologo /std:c++17 /O2 /EHsc /bigobj /W3 /wd4305 /wd4996 /I"%VULKAN_SDK%\Include" /I"%OPENPBR%" "%ROOT%\src\furnace\furnace_host.cpp" /Fe:"%OUT%\furnace_host.exe" /Fo:"%OUT%\\" /link /LIBPATH:"%VULKAN_SDK%\Lib" vulkan-1.lib
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo s1_build: host compile/link failed ^(%RC%^)
  exit /b 12
)
if not exist "%OUT%\furnace_host.exe" exit /b 12
echo s1_build: ok
exit /b 0
