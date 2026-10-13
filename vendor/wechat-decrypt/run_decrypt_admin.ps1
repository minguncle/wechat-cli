$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:WECHAT_DECRYPT_NONINTERACTIVE = '1'
Set-Location 'E:\WX\tools\wechat-decrypt'

$log = 'E:\WX\tools\wechat-decrypt\decrypt_admin.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"===== decrypt run started: $stamp =====" | Set-Content -LiteralPath $log -Encoding UTF8
python main.py decrypt *>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"===== decrypt run finished: $stamp exit=$code =====" | Tee-Object -FilePath $log -Append
Start-Sleep -Seconds 5
