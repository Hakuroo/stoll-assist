$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$migrations = Get-ChildItem ".\infra\postgres\migrations\*.sql" | Sort-Object Name

if (-not $migrations) {
    Write-Host "No hay migraciones para aplicar."
    exit 0
}

foreach ($migration in $migrations) {
    Write-Host "Aplicando $($migration.Name)..."
    Get-Content $migration.FullName -Raw |
        docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stoll -d stoll_assist
}

Write-Host "Migraciones aplicadas correctamente."
