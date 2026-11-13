import os
import streamlit as st

# Caminho para a pasta do componente
FRONTEND = os.path.join(os.path.dirname(os.path.abspath(file_)), "modules", "zeus_voice")

# Declara o componente usando a API v2 (nova)
# Nota: Em versões muito recentes, usa-se st.components.v2.component
zeus_voice = st.components.v2.component(
    "zeus_voice",
    html=os.path.join(_FRONTEND, "index.html"),
    # Se você tiver CSS/JS separados, pode apontar aqui também:
    # css=os.path.join(_FRONTEND, "style.css"),
    # js=os.path.join(_FRONTEND, "zeus_voice.js"),
)

def render_voice_component(reply=None, reply_id=0, key="zeus_voice"):
    """
    Renderiza o componente e retorna o comando de voz capturado.
    """
    # A chamada agora retorna um objeto com os valores de estado/trigger
    result = zeus_voice(
        data={"reply": reply, "reply_id": reply_id}, # Dados que vão para o JS
        default={"text": ""}, # Valor inicial
        key=key
    )
    
    # Se o componente enviou um texto (via trigger), ele estará em result.text
    if result and hasattr(result, 'text') and result.text:
        return result.text
    return None
