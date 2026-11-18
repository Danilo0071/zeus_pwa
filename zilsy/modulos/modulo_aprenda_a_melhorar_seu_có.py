"""Módulo de Auto-Desenvolvimento e Refatoração de Código em Python.

Este script demonstra práticas de engenharia de software sênior para
melhorar, limpar e otimizar código Python, promovendo o auto-desenvolvimento
através de tipagem estática, imutabilidade, tratamento de erros e legibilidade.
"""

from dataclasses import dataclass
from typing import Sequence, Protocol
import logging

# Configuração de logging profissional
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# 1. Utilização de Protocolos (Interfaces) para desacoplamento e DIP
class ProcessadorDados(Protocol):
    """Define o contrato para processamento de dados."""
    def processar(self, dados: Sequence[float]) -> float:
        ...


# 2. Uso de dataclasses para imutabilidade e representação clara de dados
@dataclass(frozen=True, slots=True)
class MetricasAnalise:
    """Armazena as métricas calculadas de forma imutável."""
    total_itens: int
    media: float
    valor_maximo: float
    valor_minimo: float


class AnalisadorFinanceiro:
    """Classe robusta que aplica princípios SOLID e Clean Code."""

    __slots__ = ("_precisao",)

    def __init__(self, precisao: int = 2) -> None:
        if precisao < 0:
            raise ValueError("A precisão não pode ser negativa.")
        self._precisao = precisao

    def processar(self, dados: Sequence[float]) -> float:
        """Processa a média dos dados aplicando encapsulamento."""
        metricas = self._calcular_metricas(dados)
        return metricas.media

    def _calcular_metricas(self, dados: Sequence[float]) -> MetricasAnalise:
        """Calcula métricas estatísticas com tratamento defensivo de erros."""
        if not dados:
            logger.warning("Tentativa de processar uma sequência de dados vazia.")
            raise ValueError("A sequência de dados não pode estar vazia.")

        # Validação de tipo em tempo de execução para garantir robustez
        if not all(isinstance(x, (int, float)) for x in dados):
            logger.error("Tipos inválidos encontrados na coleção de dados.")
            raise TypeError("Todos os elementos devem ser numéricos.")

        total = len(dados)
        # Otimização: verificação antecipada redundante, mas segura se dados mutáveis externos
        if total == 0:
            logger.critical("Erro crítico: divisão por zero evitada.")
            raise ZeroDivisionError("A sequência não pode conter zero elementos após validação.")

        media = round(sum(dados) / total, self._precisao)
        valor_max = max(dados)
        valor_min = min(dados)

        return MetricasAnalise(
            total_itens=total,
            media=media,
            valor_maximo=valor_max,
            valor_minimo=valor_min
        )


# Ponto de entrada do programa demonstrando o uso correto
if __name__ == "__main__":
    # Dados de exemplo
    transacoes = (150.50, 230.00, 99.99, 450.25, 12.50)

    # Injeção de dependência e polimorfismo
    analisador: ProcessadorDados = AnalisadorFinanceiro(precisao=2)

    try:
        resultado = analisador.processar(transacoes)
        logger.info("Processamento concluído com sucesso. Média: %s", resultado)
    except (ValueError, TypeError, ZeroDivisionError) as erro:
        logger.error("Falha na execução do processo: %s", erro)