# 一键停止：QQ / 飞书 / NapCat。
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Stop-Bot([string]$needle) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" } |
        ForEach-Object {
            Write-Host "[停止] $needle (PID $($_.ProcessId))"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

Write-Host "=== 一键停止 ===" -ForegroundColor Cyan
Stop-Bot "bot2qq.py"
Stop-Bot "bot2feishu.py"

$conn = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pid3001 = ($conn | Select-Object -First 1).OwningProcess
    Write-Host "[停止] NapCat (PID $pid3001)"
    Stop-Process -Id $pid3001 -Force -ErrorAction SilentlyContinue
}
Write-Host "完成。" -ForegroundColor Cyan
