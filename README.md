# Zeus 3.0

Arquivos: `app.py`, `github_manager.py`, `requirements.txt`. O arquivo `zeus_skills.py` é criado sozinho pelo Zeus
quando ele aprende uma habilidade nova (auto-evolução).

1. Suba esta pasta inteira para o seu repositório no GitHub (repositório privado).
2. No Streamlit Cloud, abra Settings > Secrets e cole o conteúdo de `.streamlit/secrets.toml.example` com as suas chaves.
3. Para a auto-evolução, GITHUB_API_KEY precisa de permissão Contents: Read and write no repositório,
   e GITHUB_USER / GITHUB_REPO devem apontar para ele.
4. Reboot do app. Dentro do app, o expander "Auto-Evolução e Criador de Pastas (ZIP)" cria habilidades e pastas de projeto para baixar.
