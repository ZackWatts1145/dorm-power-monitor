<#
  可选的桌面通知（仅 Windows）。
  没有配置推送 key 时，告警至少能在本机弹出来。

  powershell -File toast.ps1 -Title "标题" -Body "正文"
#>
param(
  [string]$Title = '电费提醒',
  [string]$Body = '',
  [int]$Seconds = 10
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.BalloonTipTitle = $Title
$icon.BalloonTipText = $Body
$icon.Visible = $true
$icon.ShowBalloonTip([Math]::Max(5000, $Seconds * 1000))

Start-Sleep -Seconds $Seconds
$icon.Visible = $false
$icon.Dispose()
exit 0
