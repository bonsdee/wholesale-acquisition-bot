# Point THIS PowerShell window at the local throwaway database with made-up test data.
#
# Your .env is the real system: the Supabase database, production mode, and a person doing the
# checks and the pricing. Demos, reviews and practice conversations invent sellers, and every
# table is append-only, so they must never run against it — the commands refuse to. Dot-source
# this first and they run against the local test database instead:
#
#     . .\scripts\local-test.ps1
#     uv run python -m acqbot.cli review --persona plain
#
# Only this window is affected. Close it (or open a new one) to go back to .env.
# The local database must be running first:
#     & "C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe" -D "$env:LOCALAPPDATA\pgdata" -l "$env:LOCALAPPDATA\pgdata\server.log" start

$env:ACQBOT_DATABASE_URL = "postgresql+pg8000://postgres:postgres@127.0.0.1:5432/acqbot_test"
$env:ACQBOT_ENVIRONMENT = "development"
foreach ($kind in "VIN", "PPSR", "REGO", "GUIDE", "COMPS") {
    Set-Item -Path "env:ACQBOT_${kind}_PROVIDER" -Value "stub"
}
Write-Host "This window now uses the local test database and made-up test data (.env is untouched)."
