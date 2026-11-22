import sys
from types import ModuleType
from typing import Callable, Dict

# Simulação robusta do módulo 'zeus_agente'
if "zeus_agente" not in sys.modules:
    zeus_mock = ModuleType("zeus_agente")
    
    def instalar(global_scope: Dict[str, any]) -> Callable[[str], str]:
        """Instala o agente Zeus no escopo global fornecido."""
        def processar_prompt(prompt: str) -> str:
            return f"Zeus processou com sucesso: {prompt}"
        
        global_scope["processar_prompt"] = processar_prompt
        return processar_prompt

    zeus_mock.instalar = instalar
    sys.modules["zeus_agente"] = zeus_mock

import zeus_agente

# Execução da instalação no escopo global
processar_prompt = zeus_agente.instalar(globals())