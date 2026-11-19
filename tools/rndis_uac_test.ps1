# Temporarily add 192.168.42.1/24 to the DJI RNDIS adapter, test DJI's UDP
# handshake, then remove only the address this script added.
# Run through rndis_uac_test.cmd so Windows shows the UAC prompt.

$ErrorActionPreference = 'Stop'
$ip = '192.168.42.1'
$prefix = 24
$adapter = Get-NetAdapter | Where-Object {
    $_.InterfaceDescription -match 'Remote NDIS' -and $_.Status -eq 'Up'
} | Select-Object -First 1

if (-not $adapter) {
    Write-Host '未找到已连接的 Remote NDIS 网卡。请先连接眼镜并进入 OTG 有线连接模式。' -ForegroundColor Red
    Read-Host '按回车退出'
    exit 1
}

$idx = $adapter.ifIndex
Write-Host "RNDIS: $($adapter.Name) ifIndex=$idx MAC=$($adapter.MacAddress)"
Write-Host "临时添加 $ip/$prefix（只加在接口 $idx，不改默认路由）..."
$added = $false
$existing = Get-NetIPAddress -InterfaceIndex $idx -IPAddress $ip -ErrorAction SilentlyContinue
if (-not $existing) {
    New-NetIPAddress -InterfaceIndex $idx -IPAddress $ip -PrefixLength $prefix -ErrorAction Stop | Out-Null
    $added = $true
} else {
    Write-Host "$ip 已存在，测试后不会删除它。"
}

$log = Join-Path $PSScriptRoot '..\captures\rndis-uac-test.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$python = 'C:\ProgramData\anaconda3\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

try {
    Start-Sleep -Seconds 2
    Write-Host "ping $ip 的对端 192.168.42.2 ..."
    Test-Connection -ComputerName 192.168.42.2 -Count 2 -ErrorAction SilentlyContinue |
        Format-Table -AutoSize | Out-String | Write-Host
    Write-Host '开始 15 秒 UDP 9003 握手/收流测试...'
    & $python (Join-Path $PSScriptRoot 'rndis_video_test.py') --seconds 15 2>&1 |
        Tee-Object -FilePath $log
    $code = $LASTEXITCODE
    Write-Host "测试结束，python exit=$code；日志：$log"
} finally {
    if ($added) {
        Write-Host "移除临时地址 $ip/$prefix ..."
        Remove-NetIPAddress -InterfaceIndex $idx -IPAddress $ip -PrefixLength $prefix -Confirm:$false -ErrorAction SilentlyContinue
    }
    Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Select-Object IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize | Out-String | Write-Host
}
Read-Host '按回车退出'

