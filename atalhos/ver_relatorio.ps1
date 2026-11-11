# Mostra o relatório do paper trading (com a variante de estudo) e quantos dias de custos já foram coletados.
$projeto = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $projeto '.venv\Scripts\cryptoagent.exe'
$Host.UI.RawUI.WindowTitle = 'Agente - relatório'
Set-Location $projeto
try { $Host.UI.RawUI.BufferSize = New-Object Management.Automation.Host.Size(140, 3000) } catch {}

& $exe paper relatorio
Write-Host ''
& $exe custos resumo
Write-Host ''
Read-Host 'Aperte Enter para fechar'
