param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$Label,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [ValidatePattern('^[a-zA-Z0-9_]+\.py$')][string]$ScriptName='gui_textures_hdri.py',
    [int]$StartDelay=1000
)
$ErrorActionPreference='Stop'
$testRoot=$PSScriptRoot
$repoRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$runRoot=Join-Path $repoRoot ('build/m7/installed/gui-'+$Label)
if (Test-Path -LiteralPath $runRoot) { throw 'Use a new label to preserve all prior evidence.' }
$manifestPath=(Resolve-Path -LiteralPath $Manifest).Path
$installed=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($installed.houdini_build -ne '22.0.429') { throw 'Expected tested Houdini build.' }
$houdiniExe='C:\Program Files\Side Effects Software\Houdini 22.0.429\bin\houdini.exe'
$testScript=Join-Path $testRoot $ScriptName
$null=New-Item -ItemType Directory -Path $runRoot
$output=Join-Path $runRoot 'result.json'
$stdout=Join-Path $runRoot 'stdout.log'
$stderr=Join-Path $runRoot 'stderr.log'
$inherited=[ordered]@{}
foreach($name in @('HOUDINI_USER_PREF_DIR','HOUDINI_PATH','HOUDINI_PACKAGE_SKIP','HOUDINI_NO_ENV_FILE','PXR_PLUGINPATH_NAME','PYTHONPATH')) {
    $inherited[$name]=[Environment]::GetEnvironmentVariable($name)
}
$env:RT_INSTALLED_VERIFY='1'
$env:RT_INSTALLED_MANIFEST=$manifestPath
$env:RT_INSTALLED_OUTPUT=$output
$env:RT_INSTALLED_EXIT='1'
$env:RT_INSTALLED_START_DELAY_MS=[string]$StartDelay
$env:RT_LIVE_VERIFY='1'
$env:RT_LIVE_HARNESS_ROOT=$testRoot
$env:RT_TEXTURE_ASSETS=Join-Path $repoRoot 'build/m7/gui-assets'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:RT_LIVE_DEBUG_LOG=$stderr
$env:HOUDINIRT_DEBUG='1'
$env:HOUDINIRT_VALIDATION='1'
$env:HOUDINIRT_S3_CONFIG=Join-Path $runRoot 'sampling-config.json'
'{"spp":65536,"bounces":4,"seed":101}' | Set-Content -LiteralPath $env:HOUDINIRT_S3_CONFIG -Encoding ASCII
$inventory=@(Get-Process -Name houdini,hython,husk -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,Path)
ConvertTo-Json -InputObject $inventory -Depth 5 | Set-Content -LiteralPath (Join-Path $runRoot 'existing_processes.json')
$archive=Join-Path $runRoot 'executed_harness'; New-Item -ItemType Directory -Path $archive | Out-Null
Get-ChildItem -LiteralPath $testRoot -File | Where-Object { $_.Extension -in @('.py','.ps1') } | Copy-Item -Destination $archive
$testScript=Join-Path $archive $ScriptName
$env:RT_LIVE_HARNESS_ROOT=$archive
$process=Start-Process -FilePath $houdiniExe -ArgumentList @('-foreground','-geometry=1280x720+40+40','waitforui',$testScript) -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
[ordered]@{
    pid=$process.Id;started_at=(Get-Date -Format o);process_start_time=$process.StartTime.ToUniversalTime().ToString('o')
    executable=$houdiniExe;script=$testScript;script_sha256=(Get-FileHash -LiteralPath $testScript -Algorithm SHA256).Hash.ToLower()
    manifest=$manifestPath;manifest_sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLower()
    output=$output;stdout=$stdout;stderr=$stderr;config=$env:HOUDINIRT_S3_CONFIG
    inherited_discovery_environment=$inherited;discovery_overrides='None; normal preferences and packages load unchanged'
    exit_method='Native hscript quit through a posted UI event after closing test-created panes'
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runRoot 'launch.json') -Encoding UTF8
$process | Select-Object Id,HasExited


