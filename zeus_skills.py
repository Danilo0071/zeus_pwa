import spotipy
from spotipy.oauth2 import SpotifyOAuth

def tocar_musica(nome_musica):
    try:
        client_id = st.secrets.get("SPOTIFY_CLIENT_ID")
        client_secret = st.secrets.get("SPOTIFY_CLIENT_SECRET")
        refresh_token = st.secrets.get("SPOTIFY_REFRESH_TOKEN")
        redirect_uri = "https://zeuspwa-fmt3t7spxp56vcqcpomu9k.streamlit.app/"

        if not client_id or not client_secret or not refresh_token:
            return "❌ Faltam chaves do Spotify nos Secrets."

        # Autentica usando o refresh_token
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-modify-playback-state user-read-playback-state",
            cache_path=None
        )
        
        # Força o uso do token que já temos
        token_info = auth_manager.refresh_access_token(refresh_token)
        access_token = token_info['access_token']
        
        sp = spotipy.Spotify(auth=access_token)

        # Busca a música
        results = sp.search(q=nome_musica, limit=1, type='track')
        tracks = results['tracks']['items']
        
        if not tracks:
            return f"❌ Não encontrei a música '{nome_musica}'."

        track_uri = tracks[0]['uri']
        track_name = tracks[0]['name']
        artist_name = tracks[0]['artists'][0]['name']

        # Tenta tocar no dispositivo ativo
        try:
            sp.start_playback(uris=[track_uri])
            return f"✅ Tocando agora: {track_name} - {artist_name}"
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 404:
                return "⚠️ Nenhum dispositivo ativo encontrado. Abra o Spotify no celular ou na TV primeiro e tente novamente."
            else:
                return f"❌ Erro do Spotify: {e.msg}"

    except Exception as e:
        return f"❌ Erro inesperado: {e}"


import json
import statistics
from datetime import datetime
from typing import Dict, List, Any, Tuple


def calcular_metricas_trading(dados_trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """Calcula a taxa de acerto e indicadores de desempenho dos trades simulados."""
    if not dados_trades:
        return {"taxa_acerto_stop": 0.0, "taxa_acerto_trailing": 0.0, "retorno_medio": 0.0}
    
    total = len(trades_validos := [t for t in dados_trades if isinstance(t, dict)])
    if total == 0:
        return {"taxa_acerto_stop": 0.0, "taxa_acerto_trailing": 0.0, "retorno_medio": 0.0}
    
    acertos_stop = sum(1 for t in trades_validos if t.get("resultado_stop", False))
    acertos_trailing = sum(1 for t in trades_validos if t.get("resultado_trailing", False))
    retornos = [float(t.get("retorno_percentual", 0.0)) for t in trades_validos]
    
    return {
        "taxa_acerto_stop": round((acertos_stop / total) * 100, 2),
        "taxa_acerto_trailing": round((acertos_trailing / total) * 100, 2),
        "retorno_medio": round(statistics.mean(retornos), 2) if retornos else 0.0
    }


def gerar_workflow_github_actions() -> str:
    """Gera o conteúdo YAML para o pipeline do GitHub Actions (zeus_cron.yml)."""
    workflow_conteudo = """name: Zeus Automated Analysis

on:
  schedule:
    - cron: '0 */6 * * *'
  workflow_dispatch:

jobs:
  zeus-worker:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests streamlit pandas

      - name: Run Zeus Analysis Agent
        env:
          ZEUS_API_KEY: ${{ secrets.ZEUS_API_KEY }}
        run: |
          python -c "print('Zeus Background Worker Executed Successfully')"
"""
    return workflow_conteudo.strip()


def processar_logs_estudos(conteudo_json_logs: str) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """Processa o JSON de logs brutos da pasta de estudos e retorna métricas e histórico."""
    try:
        dados = json.loads(conteudo_json_logs)
        if isinstance(dados, list):
            trades = dados
        elif isinstance(dados, dict):
            trades = dados.get("trades", [])
        else:
            trades = []
        
        metricas = calcular_metricas_trading(trades)
        return metricas, trades
    except (json.JSONDecodeError, TypeError):
        return {"taxa_acerto_stop": 0.0, "taxa_acerto_trailing": 0.0, "retorno_medio": 0.0}, []
