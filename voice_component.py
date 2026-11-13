import os
import streamlit.components.v1 as components

# Caminho para a pasta do componente
FRONTEND = os.path.join(os.path.dirname(os.path.abspath(file_)), "modules", "zeus_voice")

# Declara o componente usando a API clássica estável
zeus_voice = components.declare_component("zeus_voice", path=_FRONTEND)

def render_voice_component(reply=None, reply_id=0, key="zeus_voice"):
    """
    Renderiza o componente e retorna o comando de voz capturado.
    """
    # Envia os dados para o JS e espera um retorno
    result = zeus_voice(
        reply=reply, 
        reply_id=reply_id, 
        key=key, 
        default=None
    )
    
    # Se o componente enviou um texto, retorna ele
    if result and isinstance(result, dict) and "text" in result:
        return result["text"]
    return None
