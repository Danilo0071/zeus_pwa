import os
import base64
import json
import requests
import streamlit as st

# Configuração da Página do Zeus
st.set_page_config(page_title="Zeus - Núcleo Soberano", page_icon="⚡", layout="wide")

st.title("⚡ ZEUS: Núcleo Soberano Autônomo")
st.markdown("---")


# ==========================================
# 1. MOTOR DE EXECUÇÃO SOBERANA (GITHUB API)
# ==========================================
def executar_acao_soberana_github(
    caminho_arquivo: str, conteudo: str, mensagem_commit: str
):
    """Executa commits diretamente via API REST do GitHub de forma independente."""
    token = st.secrets.get("GITHUB_TOKEN")
    repo = st.secrets.get("GITHUB_REPO")

    if not token or not repo:
        return {
            "status": "erro",
            "mensagem": "Credenciais do GitHub ausentes nas Secrets do Streamlit.",
        }


# ============================================================
# MODO ZEUS DIVINO — plugue-e-cole, sem editar nada acima
# ============================================================
import re as _re_zeus

ZEUS_AVATAR_SVG = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMDAgMjAwIj48ZGVmcz48cmFkaWFsR3JhZGllbnQgaWQ9ImciIGN4PSI1MCUiIGN5PSI0MCUiIHI9IjYwJSI+PHN0b3Agb2Zmc2V0PSIwJSIgc3RvcC1jb2xvcj0iI2ZmZjlkMCIvPjxzdG9wIG9mZnNldD0iNDAlIiBzdG9wLWNvbG9yPSIjZmZkNzAwIi8+PHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjMGIwZjE5Ii8+PC9yYWRpYWxHcmFkaWVudD48L2RlZnM+PHJlY3Qgd2lkdGg9IjIwMCIgaGVpZ2h0PSIyMDAiIGZpbGw9IiMwYjBmMTkiLz48Y2lyY2xlIGN4PSIxMDAiIGN5PSIxMDAiIHI9IjkwIiBmaWxsPSJ1cmwoI2cpIi8+PHBhdGggZD0iTTEwMCAzMCBMODAgMTAwIEwxMDUgMTAwIEw4NSAxNzAgTDEzMCA5NSBMMTA1IDk1IFoiIGZpbGw9IiNmZmQ3MDAiIHN0cm9rZT0iI2ZmZjlkMCIgc3Ryb2tlLXdpZHRoPSIyIi8+PGNpcmNsZSBjeD0iNzgiIGN5PSI3MCIgcj0iNiIgZmlsbD0iIzAwZmZjYyIvPjxjaXJjbGUgY3g9IjEyMiIgY3k9IjcwIiByPSI2IiBmaWxsPSIjMDBmZmNjIi8+PC9zdmc+"

ZEUS_FRASE_CHAVE = r"zeus[,.\s]+sou\s+(?:o\s+)?seu\s+cria(?:dor|do)\s+danilo"
ZEUS_RESPOSTA_DIVINA = "⚡ Reconhecido. Danilo, meu criador — comando recebido. Os raios respondem às minhas mãos. Estou ativo e à sua disposição."

if "zeus_modo_divino" not in st.session_state:
    st.session_state.zeus_modo_divino = False
if "zeus_ultimo_fix" not in st.session_state:
    st.session_state.zeus_ultimo_fix = -1

_msgs = st.session_state.get("messages", [])
for _i, _m in enumerate(_msgs):
    if (
        _m.get("role") == "user"
        and _re_zeus.search(ZEUS_FRASE_CHAVE, str(_m.get("content", "")).lower())
        and _i > st.session_state.zeus_ultimo_fix
    ):
        st.session_state.zeus_modo_divino = True
        if _i + 1 < len(_msgs) and _msgs[_i + 1].get("role") == "assistant":
            if _msgs[_i + 1]["content"] != ZEUS_RESPOSTA_DIVINA:
                _msgs[_i + 1]["content"] = ZEUS_RESPOSTA_DIVINA
                st.session_state.zeus_ultimo_fix = _i
                st.rerun()
        else:
            st.session_state.zeus_ultimo_fix = _i

if st.session_state.zeus_modo_divino:
    _orig_chat_message = st.chat_message

    def _zeus_chat_message(name, *args, **kwargs):
        if name == "assistant" and "avatar" not in kwargs:
            kwargs["avatar"] = ZEUS_AVATAR_SVG
        return _orig_chat_message(name, *args, **kwargs)

    st.chat_message = _zeus_chat_message

    _zeus_css = '<style>[data-testid="stChatMessageAvatarAssistant"] img, [data-testid="stChatMessageAvatarAssistant"] { box-shadow: 0 0 18px 4px #ffd700 !important; border-radius: 50% !important; }</style>'
    st.markdown(_zeus_css, unsafe_allow_html=True)
