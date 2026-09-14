param([Parameter(Mandatory = $true)][string]$PlanPath)

$ErrorActionPreference = 'Stop'
$plan = $null
$movedOriginal = $false
$installedCandidate = $false
$readyToExit = $false
$jobDir = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($PlanPath))

try {
    $plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $processes = @()
    foreach ($processId in $plan.process_ids) {
        try {
            $process = [System.Diagnostics.Process]::GetProcessById([int]$processId)
            $null = $process.Handle
            $processes += $process
        } catch [System.ArgumentException] {
            continue
        }
    }
    $candidate = Get-Item -LiteralPath $plan.candidate
    $digest = (Get-FileHash -LiteralPath $plan.candidate -Algorithm SHA256).Hash
    if ($candidate.Length -ne $plan.size -or $digest -ne $plan.sha256) {
        throw 'The downloaded executable does not match the release.'
    }
    [System.IO.File]::WriteAllText((Join-Path $jobDir 'ready'), 'ready')
    $readyToExit = $true
    foreach ($process in $processes) {
        if (-not $process.WaitForExit(60000)) {
            throw 'The previous application is still running.'
        }
        $process.Dispose()
    }

    if (Test-Path -LiteralPath $plan.backup) {
        Remove-Item -LiteralPath $plan.backup
    }
    Move-Item -LiteralPath $plan.target -Destination $plan.backup
    $movedOriginal = $true
    Move-Item -LiteralPath $plan.candidate -Destination $plan.target
    $installedCandidate = $true
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $plan.target -WorkingDirectory ([System.IO.Path]::GetDirectoryName($plan.target))
} catch {
    $failure = $_.Exception.Message
    if ($movedOriginal) {
        try {
            if ($installedCandidate) {
                Remove-Item -LiteralPath $plan.target
            }
            Move-Item -LiteralPath $plan.backup -Destination $plan.target
            $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
            Start-Process -FilePath $plan.target -WorkingDirectory ([System.IO.Path]::GetDirectoryName($plan.target))
        } catch {
            $failure += "`nRestore failed: " + $_.Exception.Message
        }
    }
    $failure | Set-Content -LiteralPath (Join-Path $jobDir 'error.log') -Encoding UTF8
    if (-not $readyToExit) {
        exit 1
    }
    Add-Type -AssemblyName System.Windows.Forms
    $title = if ($null -ne $plan) { $plan.error_title } else { 'CNKIBug update failed' }
    $message = if ($null -ne $plan) { $plan.error_message + "`n`n" + $failure } else { $failure }
    [System.Windows.Forms.MessageBox]::Show($message, $title, 'OK', 'Error') | Out-Null
    exit 1
}
