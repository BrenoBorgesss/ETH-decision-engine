# Cobertura de testes das partes críticas

Medida em 11/11/2026 (Etapa 11), commit da branch `melhorias`, com 381 testes passando no Windows e Python 3.14.

## Como reproduzir

```bash
python -m pip install -e ".[dev]"
python -m pytest --cov --cov-report=json:coverage.json
python scripts/cobertura_critica.py coverage.json --minimo 85
```

O mesmo script roda no GitHub Actions (`.github/workflows/ci.yml`) e reprova o CI se alguma área ficar abaixo de 85%.
A porcentagem soma as linhas executadas e os dois lados de cada `if` (ramos).

## Resultado por área

| Área | Linhas | Ramos (if/else) | Cobertura | Arquivo menos coberto |
|---|---|---|---|---|
| Coleta de dados | 687/711 | 107/128 | **94.6%** | `data/market_store.py` (86.4%) |
| Indicadores | 288/295 | 21/28 | **95.7%** | `indicators/regime.py` (84.6%) |
| Sinais e decisão | 686/714 | 144/158 | **95.2%** | `engine/live.py` (87.0%) |
| Backtest | 848/888 | 147/180 | **93.2%** | `backtest/report.py` (83.8%) |
| Carteira virtual | 596/649 | 128/156 | **89.9%** | `paper/runner.py` (80.7%) |
| Registro de operações | 302/310 | 62/66 | **96.8%** | `dashboard/views.py` (89.9%) |
| Promoção de modelo | 878/916 | 151/184 | **93.5%** | `engine/reliability.py` (87.2%) |
| Risco | 276/303 | 65/76 | **90.0%** | `account/status.py` (87.0%) |
| Segurança (somente leitura, segredos) | 220/222 | 37/38 | **98.8%** | `data/rest.py` (98.5%) |
| Pacote inteiro (inclui telas do terminal) | | | 79.0% | |

Antes da Etapa 11 (mesma medição, 353 testes): pacote inteiro 74,7%; coleta 88,7% (WebSocket 33%), backtest 85,2%
(walk-forward 55%), carteira virtual 81,5% (relatório 69%), sinais 92,1% (sinais extras 77%), registro 91,2%.

## O que os testes conferem em cada área

- **Coleta:** paginação, nova tentativa com espera crescente (429/418/5xx), gravação parcial, validação de candles
  (buracos, duplicados, preços fora da escala), WebSocket contra um servidor local (entrega, erro no tratamento,
  queda e silêncio de 30 s).
- **Indicadores:** valores comparados com contas feitas à mão (ex.: ADX como no livro de Wilder) e cache que só recalcula com candle novo.
- **Sinais e decisão:** cada trava vira ESPERAR, a confiança é o produto das parcelas, sinais extras pontuam só no
  caso certo, análise ao vivo de ponta a ponta com respostas falsas da Binance num instante fixo.
- **Backtest:** os atalhos dão o mesmo resultado de chamar o motor em toda barra, nada olha o futuro (dados cortados
  ou alterados depois do instante não mudam a decisão), custos em toda operação, walk-forward escolhe só pelo treino.
- **Carteira virtual:** tamanho mínimo e passo de quantidade da Binance, atraso de execução, perda máxima em R$,
  replay do histórico igual ao laço ao vivo, relatório com comprar e segurar.
- **Registro de operações:** diário manual, ligação compra → sinal, confirmação/correção, resultado por dia (FIFO).
- **Promoção de modelo:** campeão x desafiante no mesmo período, critérios de promoção, ciclo só com o passado,
  drift com correção para amostras pequenas.
- **Risco:** sessão do dia, perda máxima (VENDER continua permitido), posição pelo saldo, idade do saldo.

## O que fica de fora, e por quê

- **Telas do terminal (`cli/`):** a maior parte só monta tabelas. Os comandos que rodam no modo demonstração
  (histórico, backtest, modelo, replay, aprendizado) são testados de ponta a ponta; os que precisam da conta real
  não, porque a chave da Binance não está ativa e esses comandos só leem e exibem o que as funções já testadas devolvem.
- **Coletor de custos (`costs/recorder.py`, 34%):** laço que grava o livro de ofertas ao vivo pelo WebSocket. As contas que
  ele usa (spread, profundidade e slippage, em `costs/orderbook.py`) são testadas; o laço em si depende da conexão real.
- **Alertas do Windows:** a regra de quando alertar é testada; a notificação nativa (via PowerShell) não é disparada nos testes.
