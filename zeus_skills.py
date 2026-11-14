import os
import base64
import requests
from urllib.parse import urlparse, parse_qs
import streamlit as st

def autorizar_spotify():
    try:
        # Pega as variáveis dos Secrets
        auth_url = st.secrets.get("SPOTIFY_AUTH_URL")
        client_id = st.secrets.get("SPOTIFY_CLIENT_ID")
        client_secret = st.secrets.get("SPOTIFY_CLIENT_SECRET")
        redirect_uri = "https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/"

        if not auth_url or not client_id or not client_secret:
            return "❌ Faltam variáveis do Spotify nos Secrets."

        # Extrai o código da URL
        parsed = urlparse(auth_url)
        query = parse_qs(parsed.query)
        code = query.get("code")
        
        if not code:
            return "❌ Código 'code' não encontrado na URL de autorização."

        code = code[0]

        # Prepara a requisição para trocar o código pelo token
        token_url = "https://accounts.spotify.com/api/token"
        
        auth_bytes = f"{client_id}:{client_secret}".encode()
        auth_header = base64.b64encode(auth_bytes).decode()
        
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }

        # Faz a requisição
        resp = requests.post(token_url, headers=headers, data=data)

        if resp.status_code == 200:
            dados = resp.json()
            refresh_token = dados.get("refresh_token")
            return f"✅ SUCESSO! Copie este código e coloque no Secrets como SPOTIFY_REFRESH_TOKEN:\n\n{refresh_token}"
        else:
            return f"❌ Erro do Spotify ({resp.status_code}): {resp.text}"

    except Exception as e:
        return f"❌ Erro ao autorizar: {e}"
