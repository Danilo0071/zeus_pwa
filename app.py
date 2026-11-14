import os
import requests
import base64
import streamlit as st
from openai import OpenAI

# ============================================================
# 1. INTERFACE
# ============================================================
st.set_page_config(page_title="ZEUS 3.0", page_icon="⚡", layout="wide")

CSS = """
<style>
.stApp { background-color: #0b0f19; color: #e6f1ff; }
[data-testid="stSidebar"] { background-color: #121826; }
h1, h2, h3 { color: #00ffcc !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
st.title("ZEUS 3.0")

# ============================================================
# 2. GERENCIAMENTO DE CHAVES (st.secrets -> os.environ)
# ============================================================
def get_key(name):
    try:
        value = st.secrets[name]
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


GEMINI_API_KEY = get_key("GEMINI_API_KEY")
GROQ_API_KEY = get_key("GROQ_API_KEY")
ANTHROPIC_API_KEY = get_key("ANTHROPIC_API_KEY")
DEEPINFRA_API_KEY = get_key("DEEPINFRA_API_KEY")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
SYS_PROMPT = "Você é Zeus, um assistente de IA futurista. Responda em português, de forma clara e objetiva."


def deepinfra_client():
    return OpenAI(api_key=DEEPINFRA_API_KEY, base_url=DEEPINFRA_BASE_URL)


# ============================================================
# 6. MEMÓRIA BÁSICA
# ============================================================
if "messages" not in st.session_state:
    st.session_state.messages = []


def historico_texto():
    """Histórico só com texto, com papéis alternados começando por 'user'."""
    saida = []
    for m in st.session_state.messages:
        if m["role"] not in ("user", "assistant") or not m.get("content"):
            continue
        if saida and saida[-1]["role"] == m["role"]:
            saida[-1]["content"] += "\n" + m["content"]
        else:
            saida.append({"role": m["role"], "content": m["content"]})
    while saida and saida[0]["role"] != "user":
        saida.pop(0)
    return saida


# ============================================================
# 3. MODELOS DE TEXTO
# ============================================================
def get_llm_response(model_choice, msgs):
    try:
        if model_choice == "gemini":
            if not GEMINI_API_KEY:
                return "❌ GEMINI_API_KEY ausente."
            contents = [
                {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                for m in msgs
            ]
            r = requests.post(
                GEMINI_URL,
                headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
                json={"contents": contents},
                timeout=60,
            )
            if r.status_code != 200:
                return f"❌ Gemini ({r.status_code}): {r.text[:300]}"
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)

        elif model_choice == "groq":
            if not GROQ_API_KEY:
                return "❌ GROQ_API_KEY ausente."
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "system", "content": SYS_PROMPT}] + msgs,
                },
                timeout=60,
            )
            if r.status_code != 200:
                return f"❌ Groq ({r.status_code}): {r.text[:300]}"
            return r.json()["choices"][0]["message"]["content"]

        elif model_choice == "claude":
            if not ANTHROPIC_API_KEY:
                return "❌ ANTHROPIC_API_KEY ausente."
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-20241022",
                    "max_tokens": 1024,
                    "system": SYS_PROMPT,
                    "messages": msgs,
                },
                timeout=60,
            )
            if r.status_code != 200:
                return f"❌ Claude ({r.status_code}): {r.text[:300]}"
            return "".join(b.get("text", "") for b in r.json()["content"])

        elif model_choice == "deepinfra":
            if not DEEPINFRA_API_KEY:
                return "❌ DEEPINFRA_API_KEY ausente."
            client = deepinfra_client()
            resp = client.chat.completions.create(
                model="cognitivecomputations/dolphin-2.9-llama3-8b",
                messages=[{"role": "system", "content": SYS_PROMPT}] + msgs,
            )
            return resp.choices[0].message.content

        return "❌ Modelo inválido."
    except Exception as e:
        return f"❌ Erro em {model_choice}: {e}"


# ============================================================
# 4. VISÃO (Gemini com inline_data em base64)
# ============================================================
def analisar_imagem(img_b64, mime, pergunta):
    if not GEMINI_API_KEY:
        return "❌ GEMINI_API_KEY ausente."
    try:
        r = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
            json={
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"text": pergunta},
                        {"inline_data": {"mime_type": mime, "data": img_b64}},
                    ],
                }]
            },
            timeout=90,
        )
        if r.status_code != 200:
            return f"❌ Gemini visão ({r.status_code}): {r.text[:300]}"
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except Exception as e:
        return f"❌ Erro na análise: {e}"


# ============================================================
# 5. GERAÇÃO DE IMAGEM (DeepInfra via cliente OpenAI)
# ============================================================
def gerar_imagem(prompt):
    if not DEEPINFRA_API_KEY:
        return None, "❌ DEEPINFRA_API_KEY ausente."
    try:
        client = deepinfra_client()
        resp = client.images.generate(model="black-forest-labs/FLUX-1-schnell", prompt=prompt)
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64), ""
    except Exception as e:
        return None, f"❌ Erro ao gerar imagem: {e}"


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("⚙️ Configuração")
    model_choice = st.selectbox("🧠 Modelo de texto", ["gemini", "groq", "claude", "deepinfra"], key="model_sel")

    st.divider()
    st.subheader("👁️ Visão")
    arquivo = st.file_uploader("Enviar imagem", type=["png", "jpg", "jpeg", "webp"], key="uploader")
    img_b64, img_mime = None, None
    if arquivo is not None:
        dados = arquivo.getvalue()
        img_b64 = base64.b64encode(dados).decode("utf-8")
        img_mime = arquivo.type or "image/jpeg"
        st.image(dados, caption="Imagem carregada", use_container_width=True)
        pergunta_img = st.text_input("Pergunta sobre a imagem", value="Descreva esta imagem.", key="pergunta_img")
        if st.button("🔍 Analisar imagem", key="btn_analisar"):
            with st.spinner("Analisando..."):
                resposta = analisar_imagem(img_b64, img_mime, pergunta_img)
            st.session_state.messages.append({"role": "user", "content": "🖼️ " + pergunta_img})
            st.session_state.messages.append({"role": "assistant", "content": resposta})

    st.divider()
    st.subheader("🎨 Gerar imagem")
    prompt_img = st.text_input("Prompt da imagem", key="prompt_img")
    if st.button("✨ Gerar", key="btn_gerar") and prompt_img.strip():
        with st.spinner("Gerando imagem..."):
            imagem, erro = gerar_imagem(prompt_img.strip())
        if imagem:
            st.session_state.messages.append({"role": "user", "content": "🎨 " + prompt_img.strip()})
            st.session_state.messages.append({"role": "assistant", "content": "", "image": imagem})
        else:
            st.error(erro)

    st.divider()
    if st.button("🗑️ Limpar conversa", key="btn_limpar"):
        st.session_state.messages = []
        st.rerun()

# ============================================================
# 7. CHAT PRINCIPAL
# ============================================================
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m.get("content"):
            st.markdown(m["content"])
        if m.get("image"):
            st.image(m["image"])

entrada = st.chat_input("Fale com o Zeus...")
if entrada:
    st.session_state.messages.append({"role": "user", "content": entrada})
    with st.chat_message("user"):
        st.markdown(entrada)
    with st.chat_message("assistant"):
        with st.spinner("Pensando..."):
            resposta = get_llm_response(model_choice, historico_texto())
        st.markdown(resposta)
    st.session_state.messages.append({"role": "assistant", "content": resposta})
