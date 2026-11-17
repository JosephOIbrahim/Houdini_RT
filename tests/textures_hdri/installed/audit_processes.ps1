param([Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$Label)
$ErrorActionPreference='Stop'
$repoRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$runRoot=Join-Path $repoRoot ('build/m7/installed/gui-'+$Label)
$launch=Get-Content -LiteralPath (Join-Path $runRoot 'launch.json') -Raw | ConvertFrom-Json
$existing=@(Get-Content -LiteralPath (Join-Path $runRoot 'existing_processes.json') -Raw | ConvertFrom-Json)
$owned=Get-Process -Id $launch.pid -ErrorAction SilentlyContinue
$ownedSame=($null -ne $owned -and $owned.StartTime.ToUniversalTime() -eq [datetime]$launch.process_start_time)
$checks=@()
foreach($record in $existing) {
 if($null -eq $record) { continue }
 $current=Get-Process -Id $record.Id -ErrorAction SilentlyContinue
 $same=($null -ne $current -and $current.StartTime -eq [datetime]$record.StartTime)
 $checks += [ordered]@{pid=$record.Id;process=$record.ProcessName;same_process_running=$same}
}
$unchanged=(@($checks | Where-Object {-not $_.same_process_running}).Count -eq 0)
[ordered]@{owned_pid=$launch.pid;owned_creation_utc=$launch.process_start_time;owned_child_exited=(-not $ownedSame)
 preexisting_unchanged=$unchanged;preexisting=$checks;observed_utc=[datetime]::UtcNow.ToString('o')
 current_houdini=@(Get-Process -Name houdini,hython,husk -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,Path)
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runRoot 'process_audit.json') -Encoding utf8
if($ownedSame) { throw 'Owned test process remains alive; no final receipt yet.' }
if(-not $unchanged) { throw 'A preexisting process changed; investigate without claiming preservation.' }
Get-Content -LiteralPath (Join-Path $runRoot 'process_audit.json')
