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
