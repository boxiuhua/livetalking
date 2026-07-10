Write-Host '正在停止数字人直播...' -ForegroundColor Cyan
$killed = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app.py*' -or $_.CommandLine -like '*autohost.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed += $_.ProcessId }
if ($killed) { Write-Host "已停止进程: $($killed -join ', ')" -ForegroundColor Green }
else { Write-Host '没有正在运行的直播进程。' -ForegroundColor Yellow }
Start-Sleep -Seconds 1
Read-Host '按回车退出'
