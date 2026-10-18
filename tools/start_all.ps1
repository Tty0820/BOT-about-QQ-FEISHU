# 一键启动：读取 config.json，不再写死路径/账号。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$configPath = Join-Path $root "config.json"
if (-not (Test-Path $configPath)) {
    Write-Host "缺少 config.json。先复制 config.example.json 为 config.json。" -ForegroundColor Red
    exit 1
}
$cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = [string]$cfg.startup.python
$napcat = [string]$cfg.startup.napcat
$quick = [int]$cfg.startup.quick_login_qq
$qqBot = Join-Path $root "bots\qq\bot2qq.py"
$fsBot = Join-Path $root "bots\feishu\bot2feishu.py"

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $port); return $true }
    catch { return $false }
    finally { $client.Close() }
}

function Get-BotProcess([string]$needle) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" }
}

Write-Host "=== 一键启动 ===" -ForegroundColor Cyan

if (Test-Port 3001) {
    Write-Host "[跳过] OneBot 3001 已在监听" -ForegroundColor Green
} elseif (-not $napcat) {
    Write-Host "[警告] config.json 里 startup.napcat 为空，无法自动启动 NapCat" -ForegroundColor Red
} else {
    Write-Host "[启动] NapCat ..." -ForegroundColor Yellow
    $cmdLine = "`"$napcat`""
    if ($quick -gt 0) { $cmdLine += " -q $quick" }
    Start-Process -FilePath "cmd.exe" -ArgumentList '/k', $cmdLine
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port 3001) { $ok = $true; break }
    }
    if ($ok) { Write-Host "[OK] 3001 已就绪" -ForegroundColor Green }
    else { Write-Host "[警告] NapCat 60 秒内没起来，检查二维码/路径" -ForegroundColor Red }
}

if (-not $cfg.qq.enabled) {
    Write-Host "[跳过] QQ 未启用" -ForegroundColor Green
} elseif (Get-BotProcess "bot2qq.py") {
    Write-Host "[跳过] QQ bot 已在运行" -ForegroundColor Green
} else {
    Write-Host "[启动] QQ bot ..." -ForegroundColor Yellow
    Start-Process -FilePath $python -ArgumentList "`"$qqBot`"" -WorkingDirectory (Join-Path $root "bots\qq")
}

if (-not $cfg.feishu.enabled) {
    Write-Host "[跳过] 飞书未启用" -ForegroundColor Green
} elseif (Get-BotProcess "bot2feishu.py") {
    Write-Host "[跳过] 飞书 bot 已在运行" -ForegroundColor Green
} else {
    Write-Host "[启动] 飞书 bot ..." -ForegroundColor Yellow
    Start-Process -FilePath $python -ArgumentList "`"$fsBot`"" -WorkingDirectory (Join-Path $root "bots\feishu")
}

Write-Host "启动完成。" -ForegroundColor Cyan
