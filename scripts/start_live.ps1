$ErrorActionPreference = 'Stop'
$py   = 'D:\miniconda3\envs\livetalking\python.exe'
$proj = 'D:\workspase\rust\LiveTalking'

Write-Host '启动数字人直播（虚拟摄像头模式）...' -ForegroundColor Cyan

if (Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue) {
  Write-Host '端口 8010 已被占用，可能已在运行。请先运行 停止直播.bat 再启动。' -ForegroundColor Yellow
  Read-Host '按回车退出'; exit 1
}

# 1) 启动服务（独立窗口显示日志）
Start-Process -FilePath 'powershell' -ArgumentList @(
  '-NoExit','-Command',
  "Set-Location '$proj'; & '$py' app.py --transport virtualcam --model wav2lip --avatar_id wav2lip256_avatar1"
) -WindowStyle Normal

Write-Host '等待数字人和虚拟摄像头启动（约 10-30 秒）...'
$ready = $false
for ($i=0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8010/api/admin/sessions' -UseBasicParsing -TimeoutSec 4
    $s = ($r.Content | ConvertFrom-Json).data.sessions
    if ($s -and ($s.sessionid -contains '0')) { $ready = $true; break }
  } catch {}
  Write-Host '.' -NoNewline
}
Write-Host ''
if (-not $ready) {
  Write-Host '服务未在预期时间内就绪，请查看服务窗口的报错。' -ForegroundColor Red
  Read-Host '按回车退出'; exit 1
}
Write-Host '数字人已就绪，虚拟摄像头已开启。' -ForegroundColor Green

# 2) 启动值守（另一个窗口，自动讲故事/唱歌）
Start-Process -FilePath 'powershell' -ArgumentList @(
  '-NoExit','-Command',
  "Set-Location '$proj'; & '$py' autohost.py --sessionid 0 --gap 3"
) -WindowStyle Normal

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ' 数字人直播已启动！' -ForegroundColor Green
Write-Host ' 下一步：抖音直播伴侣 → 添加摄像头 → 选 OBS Virtual Camera'
Write-Host ' 注意：不要打开浏览器 index.html！否则会抢占虚拟摄像头崩溃'
Write-Host ' 停止：运行 停止直播.bat'
Write-Host '========================================' -ForegroundColor Cyan
Read-Host '按回车关闭本窗口（两个服务窗口会继续运行）'
