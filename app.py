import streamlit as st
import os
from dotenv import load_dotenv

# Corrigido para os nomes exatos dos arquivos que criamos na pasta modules:
from modules.database import init_db, add_message
from modules.llm_client import get_llm_response
from modules.voice_component import render_voice_component
from modules.prtg_client import execute_prtg_action
from modules.monitor import check_logs_and_act

load_dotenv() # Localmente. Na nuvem, use st.secrets.

# Configuração da Página PWA
st.set_page_config(page_title="Zeus", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

# Injeção de Meta Tags para PWA (Streamlit permite via st.markdown)
st.markdown("""
    <link rel="manifest" href="/manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <script>
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js');
        }
    </script>
""", unsafe_allow_html=True)

# Inicialização
init_db()
if "session_id" not in st.session_state:
    st.session_state.session_id = "zeus_master"

st.title("⚡ Zeus Assistente")

# Sidebar: Configurações e Agente
with st.sidebar:
    st.header("Configuração")
    model_choice = st.selectbox("Modelo LLM", ["gemini", "groq", "claude"])
    st.divider()
    st.subheader("Agente PRTG (Ação Rápida)")
    sensor_id = st.text_input("ID do Sensor PRTG")
    new_name = st.text_input("Novo Nome (para renomear)")
    if st.button("Aplicar Alteração"):
        if sensor_id and new_name:
            ok, msg = execute_prtg_action(sensor_id, "rename", {"new_name": new_name})
            if ok: st.success(msg)
            else: st.error(msg)
    
    st.divider()
    if st.button("Verificar Logs de Rede Agora"):
        check_logs_and_act()
        st.toast("Verificação de logs executada.")

# Interface de Chat
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.markdown(msg["content"])

if prompt := st.chat_input("Fale ou digite seu comando..."):
    # Salva e exibe mensagem do usuário
    st.session_state.messages.append({"role": "user", "content": prompt})
    add_message(st.session_state.session_id, "user", prompt)
    with st.chat_message("user"): st.markdown(prompt)

    # Verifica se é um comando de ação para o PRTG
    if "prtg" in prompt.lower() or "sensor" in prompt.lower():
        st.info("Comando de agente detectado. Use a barra lateral para ações estruturadas no PRTG.")
    
    # Resposta do LLM
    with st.chat_message("assistant"):
        with st.spinner("Zeus pensando..."):
            response = get_llm_response(st.session_state.session_id, prompt, model_choice)
            st.markdown(response)
            add_message(st.session_state.session_id, "assistant", response)
            st.session_state.messages.append({"role": "assistant", "content": response})

# Renderiza componente de voz
render_voice_component()