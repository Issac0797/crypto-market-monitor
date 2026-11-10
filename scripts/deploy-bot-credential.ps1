# Transfer only the locally encrypted Bot profile through SSH stdin; never print it.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$IdentityFile
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskProfile = Import-Clixml -LiteralPath (Join-Path $taskRoot 'data/telegram-bot.clixml')
if ($taskProfile.Bot -notin @('bot', 'B') -or $taskProfile.Token -isnot [Security.SecureString]) {
    throw 'Bot encrypted profile unavailable'
}
$taskToken = [Net.NetworkCredential]::new('', $taskProfile.Token).Password
$taskChat = [long]$taskProfile.ChatId
if ($taskToken -notmatch '^\d+:[A-Za-z0-9_-]+$' -or $taskChat -eq 0) {
    throw 'Invalid Bot configuration (redacted)'
}
$taskInfo = [Diagnostics.ProcessStartInfo]::new()
$taskInfo.FileName = 'ssh.exe'
$taskInfo.UseShellExecute = $false
$taskInfo.RedirectStandardInput = $true
$taskInfo.RedirectStandardOutput = $true
$taskInfo.RedirectStandardError = $true
foreach ($taskArgument in @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12', '-o', 'IdentitiesOnly=yes',
        '-i', $IdentityFile, "root@$Server",
        'set -eu; umask 077; install -d -m 755 /etc/crypto-market-monitor; cat > /etc/crypto-market-monitor/telegram.env.new; chmod 600 /etc/crypto-market-monitor/telegram.env.new; mv /etc/crypto-market-monitor/telegram.env.new /etc/crypto-market-monitor/telegram.env')) {
    $taskInfo.ArgumentList.Add($taskArgument)
}
$taskProcess = [Diagnostics.Process]::Start($taskInfo)
try {
    $taskProcess.StandardInput.Write("CRYPTO_MARKET_MONITOR_TELEGRAM_TOKEN=$taskToken`nCRYPTO_MARKET_MONITOR_TELEGRAM_CHAT_ID=$taskChat`nCRYPTO_MARKET_MONITOR_TELEGRAM_ALLOWED_CHAT_IDS=$taskChat`n")
    $taskProcess.StandardInput.Close()
    $null = $taskProcess.StandardOutput.ReadToEnd()
    $null = $taskProcess.StandardError.ReadToEnd()
    $taskProcess.WaitForExit()
    if ($taskProcess.ExitCode -ne 0) { throw 'Encrypted SSH credential transfer failed (redacted)' }
    Write-Output 'Bot profile transferred via encrypted SSH stdin; remote root-only file created.'
} finally {
    $taskToken = $null
    $taskProfile = $null
    $taskProcess.Dispose()
}
