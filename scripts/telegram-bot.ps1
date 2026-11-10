param(
    [ValidateSet('Configure', 'Reconnect', 'Status', 'Verify', 'Test', 'Run', 'Once')][string]$Action = 'Status'
)

$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRoot 'data'
$taskProfile = Join-Path $taskData 'telegram-bot.clixml'
$taskSavedEnv = @{}
$taskExit = 0
try {
    if ($Action -eq 'Reconnect' -and -not (Test-Path -LiteralPath $taskProfile)) {
        # The original setup used terminal-scoped env vars. Reuse them when still available.
        $taskPrefix = 'CRYPTO_MARKET_MONITOR_TELEGRAM_'
        $taskPlain = [Environment]::GetEnvironmentVariable("$($taskPrefix)TOKEN", 'Process')
        $taskChatText = [Environment]::GetEnvironmentVariable("$($taskPrefix)CHAT_ID", 'Process')
        $taskAllowed = [Environment]::GetEnvironmentVariable("$($taskPrefix)ALLOWED_CHAT_IDS", 'Process')
        if ([string]::IsNullOrWhiteSpace($taskPlain)) {
            $taskPrefix = 'CQL_TELEGRAM_B_'
            $taskPlain = [Environment]::GetEnvironmentVariable("$($taskPrefix)TOKEN", 'Process')
            $taskChatText = [Environment]::GetEnvironmentVariable("$($taskPrefix)CHAT_ID", 'Process')
            $taskAllowed = [Environment]::GetEnvironmentVariable("$($taskPrefix)ALLOWED_CHAT_IDS", 'Process')
        }
        if ([string]::IsNullOrWhiteSpace($taskPlain)) {
            $taskSecret = Read-Host 'Bot token (hidden; never paste into chat)' -AsSecureString
            $taskPlain = [Net.NetworkCredential]::new('', $taskSecret).Password
        } else {
            $taskSecret = ConvertTo-SecureString -String $taskPlain -AsPlainText -Force
        }
        if ($taskPlain -notmatch '^\d+:[A-Za-z0-9_-]+$') { throw 'Invalid credential format' }
        if ([string]::IsNullOrWhiteSpace($taskChatText)) {
            $taskChatText = Read-Host 'Your numeric Chat ID (start this bot first)'
        }
        $taskChat = [long]::Parse($taskChatText)
        if ($taskChat -eq 0) { throw 'Invalid destination' }
        if ($taskAllowed -and $taskChatText -notin ($taskAllowed -split ',' | ForEach-Object { $_.Trim() })) {
            throw 'Existing destination is not allowlisted'
        }
        New-Item -ItemType Directory -Path $taskData -Force | Out-Null
        $taskConfig = [PSCustomObject]@{ Bot = 'bot'; Token = $taskSecret; ChatId = $taskChat }
        $taskNew = "$taskProfile.new"
        $taskConfig | Export-Clixml -LiteralPath $taskNew
        Move-Item -LiteralPath $taskNew -Destination $taskProfile -Force
        $taskPlain = $null
        Write-Output 'Bot profile saved encrypted locally; verifying identity and delivery now.'
    }
    if ($Action -eq 'Configure') {
        $taskSecret = Read-Host 'Bot token (hidden; never paste into chat)' -AsSecureString
        $taskPlain = [Net.NetworkCredential]::new('', $taskSecret).Password
        if ($taskPlain -notmatch '^\d+:[A-Za-z0-9_-]+$') { throw 'Invalid credential format' }
        $taskChatText = Read-Host 'Your numeric Chat ID (start the bot in Telegram first)'
        $taskChat = [long]::Parse($taskChatText)
        if ($taskChat -eq 0) { throw 'Invalid destination' }
        New-Item -ItemType Directory -Path $taskData -Force | Out-Null
        # Export-Clixml uses Windows DPAPI for SecureString: tied to this Windows user/machine.
        $taskConfig = [PSCustomObject]@{ Bot = 'bot'; Token = $taskSecret; ChatId = $taskChat }
        $taskNew = "$taskProfile.new"
        $taskConfig | Export-Clixml -LiteralPath $taskNew
        Move-Item -LiteralPath $taskNew -Destination $taskProfile -Force
        $taskPlain = $null
        Write-Output 'Bot profile saved encrypted locally. Run -Action Test to verify delivery.'
        return
    }
    if (Test-Path -LiteralPath $taskProfile) {
        $taskConfig = Import-Clixml -LiteralPath $taskProfile
        if ($taskConfig.Bot -notin @('bot', 'B') -or $taskConfig.Token -isnot [Security.SecureString]) {
            throw 'Invalid local Bot profile'
        }
        $taskValues = @{
            TOKEN = [Net.NetworkCredential]::new('', $taskConfig.Token).Password
            CHAT_ID = [string]$taskConfig.ChatId
            ALLOWED_CHAT_IDS = [string]$taskConfig.ChatId
        }
        foreach ($taskField in $taskValues.Keys) {
            $taskName = "CRYPTO_MARKET_MONITOR_TELEGRAM_$taskField"
            $taskSavedEnv[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
            [Environment]::SetEnvironmentVariable($taskName, $taskValues[$taskField], 'Process')
        }
    }
    if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Project Python environment unavailable' }
    Push-Location -LiteralPath $taskRoot
    try {
        $taskArgs = @('-m', 'crypto_market_monitor.bot_cli')
        switch ($Action) {
            'Status' { $taskArgs += '--status' }
            'Verify' { $taskArgs += @('--verify', '--telegram') }
            'Test' { $taskArgs += @('--test-message', '--telegram') }
            'Reconnect' { $taskArgs += @('--test-message', '--telegram') }
            'Once' { $taskArgs += @('--once', '--telegram') }
            'Run' { $taskArgs += '--telegram' }
        }
        & $taskPython @taskArgs
        $taskExit = $LASTEXITCODE
    } finally { Pop-Location }
} catch {
    Write-Error 'Bot setup/connection failed (details redacted). Check the local profile and configuration.' -ErrorAction Continue
    $taskExit = 1
} finally {
    foreach ($taskName in $taskSavedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($taskName, $taskSavedEnv[$taskName], 'Process')
    }
    $taskPlain = $null
    $taskConfig = $null
    $taskSecret = $null
    $taskValues = $null
}
exit $taskExit
