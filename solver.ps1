param(
  [Parameter(Mandatory=$true, Position=0)] [string] $Container,
  [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Args
)
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$solver = Join-Path $repo "external\solver\solver.py"
$containerAbs = (Resolve-Path $Container).Path
Push-Location (Split-Path $solver -Parent)
& $py $solver $containerAbs @Args
$code = $LASTEXITCODE
Pop-Location
exit $code
