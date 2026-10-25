param([string]$Distribution)
$ErrorActionPreference = 'Stop'
try {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw 'WSL is not installed. Follow the Windows/WSL steps in README.md first.'
    }
    $raw = & wsl.exe --list --quiet
    if ($LASTEXITCODE -ne 0) { throw 'WSL is not ready. Open your Ubuntu distribution once and finish creating a Linux user.' }
    $distros = @($raw | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
    if ($distros.Count -eq 0) { throw 'No Linux distribution found. Install Ubuntu 24.04 or newer, open it once, then retry.' }
    if ($Distribution) {
        if ($distros -notcontains $Distribution) { throw 'The requested Linux distribution is not installed.' }
    } elseif ($distros.Count -eq 1) {
        $Distribution = $distros[0]
    } else {
        Write-Host 'Choose your Ubuntu/Linux distribution (not a Docker-managed distribution):'
        for ($i = 0; $i -lt $distros.Count; $i++) { Write-Host ("[{0}] {1}" -f ($i + 1), $distros[$i]) }
        $choice = Read-Host 'Number'
        $number = 0
        if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $distros.Count) { throw 'Invalid choice.' }
        $Distribution = $distros[$number - 1]
    }
    Write-Host 'Only user-level Linux application files are installed. Network settings are not changed.'
    $linuxPath = (& wsl.exe -d $Distribution -- wslpath -a -u $PSScriptRoot | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $linuxPath) { throw 'Cannot translate the extracted package path. Try placing it on a local Windows drive.' }
    & wsl.exe -d $Distribution -- bash ($linuxPath + '/install.sh')
    exit $LASTEXITCODE
} catch {
    Write-Error $_
    exit 1
}