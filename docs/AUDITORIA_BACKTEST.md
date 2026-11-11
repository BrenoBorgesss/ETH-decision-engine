# Auditoria do backtest e do modelo

Revisão feita na Etapa 6 (novembro de 2026), sobre o histórico real baixado da Binance
(ETHUSDT e ETHBRL, 01/09/2025 a 22/10/2026) e sobre a amostra do repositório (`exemplos/`).
Todos os números abaixo vêm de execuções reais; os comandos para repetir estão no fim.

**Resumo:** não foi encontrado vazamento de informação do futuro no código. Os testes de corte
dão resultados idênticos. Também não foi encontrada vantagem da estratégia: fora da amostra ela
perde dinheiro depois dos custos, e o modelo desafiador não prevê melhor que um sorteio. Por isso,
a regra de confiança mantém o agente em **AGUARDAR (estratégia em teste)**.

## 1. Olhar o futuro (look-ahead)

| O que foi verificado | Como | Resultado |
|---|---|---|
| Todos os indicadores (EMA, RSI, MACD, Stoch RSI, ATR, Bollinger, ADX, VWAP do dia, volume relativo, fluxo taker, pivôs, estrutura, divergências, pavios, regime) | `tests/test_sem_futuro.py`: calcula com o histórico inteiro e com o histórico cortado em 35%, 60% e 85%. Num segundo teste, troca os preços e volumes depois do corte. As linhas anteriores ao corte precisam ser **idênticas** (tolerância zero). | Idênticas em 5m, 15m, 1h e 4h. |
| Se o método pega um vazamento | Indicador falso que usa o fechamento do próximo candle. | Pego: o valor muda na última linha antes do corte. |
| A simulação inteira, com a análise por horário ligada | Backtest do ETHBRL no histórico real inteiro x histórico cortado em 15/03, 10/06 e 05/09/2026. Compara entrada, saída, resultado, motivo e confiança das operações encerradas antes do corte. | 20, 29 e 38 operações, idênticas nos três cortes. |
| Pivôs e divergências | Leitura do código (`indicators/structure.py`). | O pivô do candle j só aparece no candle j + k, depois de confirmado. |
| Execução | Leitura do código e testes existentes. | Decisão só com candles **fechados** antes do instante. Compra na abertura do candle de 1m **seguinte**. A OCO só confere candles a partir da entrada. Stop e alvo no mesmo candle contam como stop (pior caso). |
| Análise por horário no backtest | `backtest/season_pit.py`. Coberta também pelo teste de corte acima. | Os padrões de cada mês são aprendidos só com meses anteriores. O "movimento agora" é comparado só com os 90 dias anteriores. |
| Rótulos do modelo | `tests/test_model.py`. | No treino de cada mês só entram momentos cujo **resultado** já era conhecido antes do início do mês de teste. |

### Vazamentos leves que continuam (conhecidos e medidos)

1. **Spread por hora medido depois do período testado.** A tabela de spread vem do coletor, que
   rodou de 22/10 a 11/11/2026, e é aplicada ao período de maio a setembro (22 das 24 horas
   medidas). Efeito medido trocando pelo spread padrão do config (0,04%) em todas as horas:
   - com o spread medido: 13 operações, −3,67%;
   - com o spread padrão: 12 operações, −3,21%.

   A conclusão não muda. Não existe histórico de order book para medir o spread daquela época.
2. **Taxa da conta lida hoje.** Quando há chave, a taxa vem da conta no momento do backtest. Nos
   números deste documento a taxa veio do config (0,1% por lado), porque a chave está recusada.

## 2. Backtest x ao vivo (o que roda é o que foi testado?)

- **Mesmo motor:** backtest e ao vivo usam o mesmo `decide()`.
- **Mesmos indicadores:** o ao vivo calcula os indicadores com os últimos 500 a 1.000 candles, e o
  backtest com o histórico inteiro. Em 25 instantes por timeframe, a maior diferença relativa nas
  colunas que o motor usa foi de 3,0e-5 (EMA 200 do 4h). Nas demais foi zero ou perto disso, e o
  regime e a estrutura coincidiram em 25 de 25.
- **B2 do diagnóstico:** com posição aberta, o backtest passa a lista de suportes e resistências
  vazia e o ao vivo passa os níveis. Nenhuma regra de saída usa os níveis, e o teste
  `test_com_posicao_os_suportes_e_resistencias_nao_mudam_a_decisao` garante isso daqui em diante.
- **Coerência observada:** em 5 de 6 instantes em que o backtest da amostra comprou, a análise ao
  vivo simulada (`tests/test_live.py`) também decidiu COMPRAR.

### Replay x backtest (Etapa 7)

O replay roda o laço do paper trading ao vivo sobre o histórico. Ele confirma que o caminho ao vivo
decide como o backtest:
- **Sem ajuste:** de 01 a 07/09/2026 o backtest comprou uma vez e o replay nenhuma. O replay tem
  dois avisos de dados que o backtest não tem (derivativos e perfil por horário indisponíveis), e
  cada aviso reduz a confiança em 5%.
- **Com a penalidade dos avisos zerada:** o replay fez a mesma compra, no mesmo minuto (01/09
  15:20), com a mesma saída (stop).

## 3. Ajuste excessivo (overfitting) e tamanho da amostra

1. **Parâmetros padrão escolhidos à mão.** Foram definidos antes desta revisão. **Não sei** se
   foram escolhidos olhando estes mesmos meses. Se foram, o período "fora da amostra" não é
   intocado, e o resultado real pode ser pior que o mostrado.
2. **Muitas variantes no mesmo relatório.** São 12 linhas: padrão, walk-forward, versão ingênua,
   2 travas e 7 de ablação. Escolher a melhor depois de ver a tabela é ajuste excessivo. Exemplo:
   "+ stoch_rsi" deu −0,48% com só 8 operações; com tão poucas operações, isso não distingue sorte
   de sinal.
3. **Amostra pequena.** A variante padrão teve 13 operações em 5 meses, com média de −0,280% por
   operação. O intervalo de 95% vai de −0,626% a +0,066%: não dá para afirmar nem ganho nem perda
   com segurança. Por isso o intervalo agora aparece no relatório.
4. **Walk-forward escolhia a "menos ruim".** Em todos os 5 meses, até a melhor combinação da grade
   perdeu dinheiro no treino (média de −0,32% a −0,41% por operação). Mesmo assim, ela era
   escolhida e operava no mês seguinte: 13 operações e −4,21%.
   - **Corrigido:** o mês só opera se a melhor combinação tiver média positiva no treino
     (`backtest.expectativa_min_treino_pct`).
   - **Resultado atual:** 0 operações; os 5 meses ficam sem operar, com o motivo registrado.
5. **A trava de distância não tem efeito (B3).** As variantes de 2,5×, 1,5× e 1× o custo dão
   resultados idênticos.
   - **Motivo:** a trava de risco/retorno mínimo (1,0 depois dos custos) já é mais exigente.
     Risco/retorno = (distância − custo) ÷ (risco + custo) ≥ 1 implica distância ≥ risco + 2 × custo.
   - **Nos dados:** dos 557 sinais que passam no risco/retorno, o menor alvo estava a 2,56× o custo.
   - **Consequência:** essas variantes não testam nada, e na grade do walk-forward o parâmetro
     `distancia_alvo_min_custo` não muda nada. Para estudar a distância, seria preciso variar
     também `rr_minimo`.

## 4. Modelo desafiador (regressão logística)

- **Conjunto de dados:** 1.033 momentos candidatos no ETHBRL. Um momento candidato tem
  esticamento, gatilho e níveis, sem trava de custo nem de segurança. Desses, 32,1% terminariam no
  lucro depois dos custos, com resultado médio de −0,217%.
- **Qualidade da previsão fora da amostra (5 meses):**
  - AUC 0,486, ou seja, igual a um sorteio (0,5).
  - Erro de Brier 0,256, pior que prever sempre a taxa de acerto do treino (0,224).
  - Os 22 momentos com probabilidade prevista acima de 60% acertaram 13,6%, menos que a média.
- **Operando no simulador:** o desafiador fez 30 operações, −3,68%, média de −0,129% por operação
  (intervalo de 95%: −0,295% a +0,037%). As regras fizeram 13 operações e −3,67%. Comprar e
  segurar deu +24,02% (drawdown −33,80%).
- **Conclusão:** o modelo não aprendeu nada útil com estas variáveis e esta amostra. Ele não
  substitui as regras. Fica registrado como desafiador para a etapa de aprendizado contínuo.

## 4b. Aprendizado contínuo (Etapa 8)

- **Seis ciclos ponto no tempo no histórico real** (até 01/06, 01/07, 01/08, 01/09, 01/10 e 22/10/2026):
  nenhum desafiante foi promovido.
  - **Hipótese do setup 1h:** teve 2 operações fora da amostra, amostra insuficiente.
  - **Modelo:** no último ciclo, 30 operações e −0,129% por operação, com retorno pior que o das regras.
- **Calibração:** a confiança das regras não acompanha o acerto. Em todas as faixas, de 0% a 100% de
  confiança, o acerto ficou entre 20% e 34%.
- **Panoramas por cenário:** com 42 cenários olhados de uma vez e casos vizinhos parecidos, um
  cenário com intervalo de 95% acima de zero não é evidência de vantagem. Ele só pode virar
  estratégia como hipótese declarada e testada fora da amostra.
- **Alerta de mudança de regime:** a primeira versão alertava em todos os ciclos, por efeito de
  amostra pequena, comparações múltiplas e memória das séries. A versão final compara o último mês
  com as outras janelas de 30 dias do período e alerta em 1 dos 6 ciclos (volatilidade em junho).

## 5. Correções feitas nesta etapa

| ID | Problema | Correção |
|---|---|---|
| B4 | O Sharpe montava a série diária com dias UTC, mas as operações usam dias de Brasília; uma operação na véspera UTC sumia. | A série usa os dias locais. O teste mostra o Sharpe NaN no código antigo e calculado no novo. |
| B5 | Comprar e segurar usava taxa fixa de 0,1% e ignorava spread e slippage. | Usa a taxa, o spread médio e o slippage do backtest, na mesma conta de uma operação. +24,07% → +24,02%. |
| M5 | Escolha do walk-forward sem medida de incerteza, e operando mesmo com treino negativo. | Intervalo de 95% no relatório; mês sem ganho no treino não opera. |
| M3 | Execução não reprodutível. | Custos congelados (Etapa 3) e procedência gravada em `backtest.meta.json` e `modelo.meta.json`. A procedência registra commit, parâmetros e o hash deles, custos, hash de cada arquivo de dados, semente e hash do relatório. Treinar duas vezes dá um arquivo de modelo idêntico byte a byte. |

## 6. Como repetir

```
python -m pytest tests/test_sem_futuro.py tests/test_model.py tests/test_reliability.py
cryptoagent backtest --pares ETHBRL --custos-arquivo custos.json
cryptoagent modelo treinar --par ETHBRL --custos-arquivo custos.json
cryptoagent --demo modelo treinar        # sem internet e sem chave, na amostra do repositório
```

O hash dos parâmetros e o de cada arquivo de dados ficam em `data/reports/backtest.meta.json`.
Com os mesmos arquivos e o mesmo `custos.json`, o relatório sai igual byte a byte.
