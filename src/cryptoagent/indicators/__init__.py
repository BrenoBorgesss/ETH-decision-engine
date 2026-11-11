"""Indicadores técnicos, escritos à mão para serem auditáveis.

Convenções (iguais às de TA-Lib / StockCharts):
- Médias exponenciais (EMA) e de Wilder (RMA) começam com a média simples dos
  primeiros N valores. Antes disso o valor é NaN (aquecimento).
- Nada olha para o futuro: o valor no candle i usa só candles até i. Pivôs de topo/fundo
  só aparecem quando estão confirmados (N candles depois).
- Entradas são DataFrames de candles (colunas open, high, low, close, volume...).
"""
