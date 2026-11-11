# Abre o agente com um clique: coletor de custos, paper trading e painel, cada um na sua janela.
# O que já estiver rodando não é aberto de novo. Para parar um deles: Ctrl+C na janela dele (ou feche a janela).
$ErrorActionPreference = 'Stop'
$projeto = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $projeto '.venv\Scripts\cryptoagent.exe'

if (-not (Test-Path $exe)) {
    Write-Host "Não achei o agente em $exe" -ForegroundColor Red
    Read-Host 'Aperte Enter para fechar'
    exit 1
}

$rodando = @(Get-CimInstance Win32_Process -Filter "Name = 'cryptoagent.exe'" | ForEach-Object { $_.CommandLine })

$tarefas = @(
    @{ Titulo = 'Agente - coleta de custos'; Comando = 'custos coletar' },
    @{ Titulo = 'Agente - paper trading';    Comando = 'paper rodar' },
    @{ Titulo = 'Agente - painel';           Comando = 'painel' }
)

foreach ($t in $tarefas) {
    if ($rodando | Where-Object { $_ -match [regex]::Escape($t.Comando) }) {
        Write-Host "Já está rodando: $($t.Titulo)" -ForegroundColor Yellow
        continue
    }
    $script = "`$Host.UI.RawUI.WindowTitle = '$($t.Titulo)'; Set-Location '$projeto'; & '$exe' $($t.Comando)"
    Start-Process powershell.exe -WorkingDirectory $projeto -ArgumentList '-NoExit', '-NoProfile', '-Command', $script
    Write-Host "Aberto: $($t.Titulo)" -ForegroundColor Green
    Start-Sleep -Seconds 2
}

Write-Host ''
Write-Host 'Pronto. Deixe as janelas abertas (pode minimizar). Nenhuma ordem é enviada à Binance.'
Start-Sleep -Seconds 6
