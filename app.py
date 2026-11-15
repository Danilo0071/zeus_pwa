# ============================================================
# BLOCO 4: AVATAR 3D (LENDO ARQUIVO EXTERNO) E INTERFACE
# ============================================================

def render_avatar_core(avatar_key):
    # Procura o arquivo html que está na mesma pasta do app.py
    caminho_html = pathlib.Path(_file_).parent / "avatar_zeus.html"
    
    if caminho_html.exists():
        # Lê o arquivo e renderiza o HTML puro, sem quebrar o Python
        html_content = caminho_html.read_text(encoding="utf-8")
        components.html(html_content, height=650)
    else:
        st.error("⚠️ Arquivo avatar_zeus.html não encontrado no repositório. Verifique se você o criou.")

AVATAR_MAP = {
    "Esfera 3D (Cyberpunk)": "sphere",
    "⚡ Deuses Jogando Raios": "gods_lightning",
    "Olho Cibernético": "eye",
    "Robô Digital": "robot"
}

# ============================================================
# APP PRINCIPAL E INTERFACE LATERAL
# ============================================================
init_db()
if "session_id" not in st.session_state: st.session_state.session_id = "zeus_master"
if "messages" not in st.session_state: st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ Configuração Zeus 5.0")
    theme_name = st.selectbox("🎨 Tema Visual", list(THEMES.keys()), index=0)
    avatar_label = st.selectbox("🤖 Avatar Ativo", list(AVATAR_MAP.keys()), index=0)
    avatar_key = AVATAR_MAP[avatar_label]
    model_choice = st.selectbox("🧠 Modelo LLM Principal", PROVIDERS, index=0, key="model_sel")
    
    st.divider()
    st.subheader("🎵 Controle Spotify")
    musica_input = st.text_input("Nome da faixa:", key="sidebar_musica")
    if st.button("▶️ Tocar no Spotify"):
        if musica_input.strip():
            st.toast(zeus_tocar_musica(musica_input), icon="🎵")

    st.divider()
    st.subheader("📂 Módulos no GitHub")
    if st.button("🔄 Atualizar Lista de Módulos"):
        st.session_state.modulos_github = zeus_listar_memoria_github()
    for mod in st.session_state.get("modulos_github", []):
        st.text(f"• {mod}")

    st.divider()
    if st.button("🗑️ Limpar Histórico"):
        st.session_state.messages = []
        st.rerun()

# Aplica o tema
inject_theme(theme_name)
st.title("⚡ ZEUS // CYBER-CORE 5.0")

# Renderiza o Avatar (Se o arquivo html estiver lá, ele aparece)
render_avatar_core(avatar_key)

# Área de Chat Padrão (para você conversar com as IAs)
with st.expander("💬 Chat Neural (Conversar com Zeus)", expanded=True):
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Digite seu comando para o Zeus..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): 
            st.markdown(prompt)
        
        with st.chat_message("assistant"):
            with st.spinner("Processando..."):
                resposta = processar_prompt(prompt)
                st.markdown(resposta)
        st.rerun()

# Painel de Auto-Evolução
with st.expander("⚡ Núcleo de Auto-Evolução (Criar no GitHub)", expanded=False):
    instrucao = st.text_input("O que o Zeus deve desenvolver e gravar no GitHub?", placeholder="Ex: criar script para análise financeira...")
    if st.button("🚀 Gerar e Salvar no GitHub"):
        if instrucao.strip():
            with st.spinner("Gerando módulo e sincronizando com GitHub..."):
                res = zeus_aprender_ensemble(instrucao.strip(), "python")
            st.markdown(res)
        else:
            st.warning("Insira uma instrução válida.")
