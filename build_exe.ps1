# 打包服务器查询器（Windows 桌面服务器巡检工具）
# 依赖：Python 3.11+（py 启动器）+ PyInstaller + paramiko
#       安装：py -m pip install pyinstaller paramiko
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1              # 默认 onedir（启动快）
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Mode onefile   # 单文件
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -NoTest          # 跳过自测

param(
    [ValidateSet('onedir', 'onefile')][string]$Mode = 'onedir',
    [switch]$NoTest
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not $NoTest) {
    Write-Host '[0/4] 自测（目标解析 / 设置读写 / 失败路径）...'
    & py -X utf8 -m servercheck --self-test --log "$env:TEMP\servercheck-selftest.txt"
    if ($LASTEXITCODE -ne 0) { throw '自测未通过，已中止打包' }
}

Write-Host '[1/4] 生成图标 server_icon.ico / server_icon.png ...'
& py -X utf8 "$root\tools\make_icon.py" "$root\server_icon.ico" "$root\server_icon.png"
New-Item -ItemType Directory -Force -Path "$root\.build" | Out-Null

# 只裁掉明显用不到的大件；paramiko / cryptography 的依赖一个都不能动。
$excludes = @(
    'numpy', 'pandas', 'matplotlib', 'scipy', 'IPython', 'pytest',
    'tkinter.test', 'tkinter.tix', 'unittest', 'pydoc', 'doctest', 'turtle', 'turtledemo'
)
$excludeArgs = @()
foreach ($m in $excludes) { $excludeArgs += @('--exclude-module', $m) }

Write-Host "[2/4] 打包（模式：$Mode）..."
$common = @(
    '--windowed', '--name', '服务器查询器',
    '--icon', "$root\server_icon.ico",
    '--add-data', "$root\server_icon.png;.",
    '--distpath', "$root\dist",
    '--workpath', "$root\.build\work",
    '--specpath', "$root\.build",
    '--noconfirm'
) + $excludeArgs
if ($Mode -eq 'onefile') { $common = @('--onefile') + $common } else { $common = @('--onedir') + $common }
$common += "$root\servercheck_gui.py"
& py -m PyInstaller @common
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

Write-Host ''
if ($Mode -eq 'onefile') {
    $exe = Get-Item "$root\dist\服务器查询器.exe"
    Write-Host ("[3/4] 完成: {0}  ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)) -ForegroundColor Green
} else {
    $dir = Get-Item "$root\dist\服务器查询器"
    $size = (Get-ChildItem $dir.FullName -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("[3/4] 完成: {0}\服务器查询器.exe  ({1:N1} MB，整个文件夹一起移动)" -f $dir.FullName, ($size / 1MB)) -ForegroundColor Green
}
Write-Host '[4/4] 目标列表与 SSH 主机信息保存在 %APPDATA%\ServerCheck\settings.json（密码不落盘）。'
