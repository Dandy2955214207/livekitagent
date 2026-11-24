$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $ProjectRoot '.runtime/uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.tools/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $ProjectRoot '.tools/python-bin'
$env:UV_NO_MODIFY_PATH = '1'
$env:npm_config_cache = Join-Path $ProjectRoot '.runtime/npm-cache'
$UvExe = Join-Path $ProjectRoot '.tools/uv/uv.exe'
