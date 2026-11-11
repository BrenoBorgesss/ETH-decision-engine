"""Modelo desafiador: regressão logística que estima se uma compra candidata termina no lucro depois dos custos.

As regras fixas (`engine/`) são o campeão. Este pacote monta o conjunto de dados a partir do mesmo
simulador do backtest, treina em walk-forward, compara com as regras fora da amostra e salva o
modelo em JSON. A troca de campeão (promoção) é decidida na etapa de aprendizado contínuo.
"""
