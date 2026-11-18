# ⚡ ZEUS 3.0 — IA Autônoma & Olimpo 3D (100% funcional)
# Como rodar:
#   pip install -r requirements.txt
#   streamlit run app.py
# Chaves: use a barra lateral do app OU arquivo .env OU .streamlit/secrets.toml

import streamlit as st
import os
import requests
import json
import base64
import time
from datetime import datetime
from pathlib import Path
import urllib.parse

st.set_page_config(
    page_title="Zeus 3.0 - IA Autônoma & Olimpo 3D",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main { background-color: #020205; color: #e8f0ff; }
    .stSidebar { background-color: #060913; border-right: 1px solid #1a2342; }
    h1, h2, h3 { color: #00ffff !important; font-family: 'Courier New', Courier, monospace; letter-spacing: 1px; }
    .stButton>button {
        background: linear-gradient(135deg, #00ffff, #ff00ff);
        color: #020205; border: none; border-radius: 6px; font-weight: bold; width: 100%;
        font-family: 'Courier New', Courier, monospace; text-transform: uppercase;
    }
    .stButton>button:hover { background: linear-gradient(135deg, #00cccc, #cc00cc); color: #fff; }
    .status-ok { color: #00ff88; } .status-fail { color: #ff4444; }
    </style>
""", unsafe_allow_html=True)

# ============================================================
# 1. SEGREDOS / CHAVES
# ============================================================
def _secret(nome: str) -> str:
    # 1) session_state (digitado na sidebar)
    try:
        if nome in st.session_state and st.session_state[nome]:
            return str(st.session_state[nome]).strip()
    except Exception:
        pass
    # 2) env
    val = os.getenv(nome, "").strip()
    if val:
        return val
    # 3) secrets.toml
    try:
        if hasattr(st, "secrets") and nome in st.secrets:
            return str(st.secrets[nome]).strip()
    except Exception:
        pass
    return ""

PROVIDERS = ["Groq", "Gemini", "Anthropic", "DeepInfra", "OpenRouter", "DeepSeek", "HuggingFace", "GitHub"]
KEYS = {
    "Groq": "GROQ_API_KEY",
    "Gemini": "GEMINI_API_KEY",
    "Anthropic": "ANTHROPIC_API_KEY",
    "DeepInfra": "DEEPINFRA_API_KEY",
    "OpenRouter": "OPENROUTER_API_KEY",
    "DeepSeek": "DEEPSEEK_API_KEY",
    "HuggingFace": "HUGGINGFACE_API_KEY",
    "GitHub": "GITHUB_API_KEY",
}

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

MEM_DIR = Path("memoria_evolucao")
MEM_DIR.mkdir(exist_ok=True)

# ============================================================
# 2. MEMÓRIA / AUTO-EVOLUÇÃO
# ============================================================
def salvar_local(topico, conteudo):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = MEM_DIR / f"{topico}_{ts}_{abs(hash(conteudo)) % 999999}.txt"
        fname.write_text(conteudo, encoding="utf-8")
        return True, str(fname)
    except Exception as e:
        return False, str(e)

def salvar_aprendizado_github(conteudo_aprendido, topico="aprendizado_zeus"):
    ok_local, path_local = salvar_local(topico, conteudo_aprendido)
    gh_token = _secret("GITHUB_API_KEY")
    gh_user = _secret("GITHUB_USER")
    gh_repo = _secret("GITHUB_REPO")
    if not (gh_token and gh_user and gh_repo):
        return ok_local, f"Salvo local ({path_local}). GitHub: credenciais ausentes (preencha GITHUB_USER/REPO na sidebar)."
    path = f"memoria_evolucao/{topico}_{abs(hash(conteudo_aprendido)) % 99999999}.txt"
    url = f"https://api.github.com/repos/{gh_user}/{gh_repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    message = f"Zeus 3.0: aprendizado autonomo sobre {topico}"
    encoded = base64.b64encode(conteudo_aprendido.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": encoded, "branch": "main"}
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            return True, f"Salvo local + GitHub ({path}) ✅"
        return ok_local, f"Salvo local. GitHub respondeu {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return ok_local, f"Salvo local. Erro GitHub: {e}"

def ler_memoria_local(n=10):
    try:
        files = sorted(MEM_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
        return [(f.name, f.read_text(encoding="utf-8")[:2000]) for f in files]
    except Exception:
        return []

# ============================================================
# 3. CASCATA DE IAs — tenta uma, se falhar vai pra próxima
# ============================================================
SYSTEM_DEFAULT = (
    "Você é o Zeus 3.0, uma inteligência artificial soberana, avançada, cinematográfica e totalmente autônoma, "
    "criada pelo Danilo (criador da Nilo). Responda com máxima profundidade técnica, precisão exata, "
    "português brasileiro claro, formatação markdown quando útil, e aprenda a cada prompt."
)

def _chamar_groq(mensagem, system_prompt):
    k = _secret("GROQ_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000, "temperature": 0.7},
        timeout=40)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "Groq ✅"
    return None, f"Groq {r.status_code}: {r.text[:200]}"

def _chamar_gemini(mensagem, system_prompt):
    k = _secret("GEMINI_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": k},
        json={"contents": [{"parts": [{"text": system_prompt + "\n\n" + mensagem}]}]},
        timeout=40)
    if r.status_code == 200:
        j = r.json()
        txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        return txt, "Gemini ✅"
    return None, f"Gemini {r.status_code}: {r.text[:200]}"

def _chamar_anthropic(mensagem, system_prompt):
    k = _secret("ANTHROPIC_API_KEY")
    if not k or not Anthropic:
        return None, "sem chave/lib"
    cl = Anthropic(api_key=k)
    resp = cl.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=4000,
                              system=system_prompt, messages=[{"role": "user", "content": mensagem}])
    txt = "".join(b.text for b in resp.content if hasattr(b, "text"))
    return (txt, "Anthropic ✅") if txt.strip() else (None, "resposta vazia")

def _chamar_deepinfra(mensagem, system_prompt):
    k = _secret("DEEPINFRA_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://api.deepinfra.com/v1/openai/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        json={"model": "meta-llama/Llama-3.3-70B-Instruct",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000},
        timeout=40)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "DeepInfra ✅"
    return None, f"DeepInfra {r.status_code}: {r.text[:200]}"

def _chamar_openrouter(mensagem, system_prompt):
    k = _secret("OPENROUTER_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://zeus30.local", "X-Title": "Zeus 3.0"},
        json={"model": "meta-llama/llama-3.3-70b-instruct",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000},
        timeout=40)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "OpenRouter ✅"
    return None, f"OpenRouter {r.status_code}: {r.text[:200]}"

def _chamar_deepseek(mensagem, system_prompt):
    k = _secret("DEEPSEEK_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000, "temperature": 0.7},
        timeout=40)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "DeepSeek ✅"
    return None, f"DeepSeek {r.status_code}: {r.text[:200]}"

def _chamar_hf(mensagem, system_prompt):
    k = _secret("HUGGINGFACE_API_KEY")
    if not k:
        return None, "sem chave"
    # Inference Providers (OpenAI-compatible)
    r = requests.post(
        "https://router.huggingface.co/v1/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        json={"model": "meta-llama/Llama-3.3-70B-Instruct",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000},
        timeout=60)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "HuggingFace ✅"
    return None, f"HF {r.status_code}: {r.text[:200]}"

def _chamar_github_models(mensagem, system_prompt):
    k = _secret("GITHUB_API_KEY")
    if not k:
        return None, "sem chave"
    r = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        json={"model": "openai/gpt-4o",
              "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": mensagem}],
              "max_tokens": 4000},
        timeout=40)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"], "GitHub Models ✅"
    return None, f"GitHub {r.status_code}: {r.text[:200]}"

CASCADE = [
    ("Anthropic", _chamar_anthropic),
    ("Groq", _chamar_groq),
    ("Gemini", _chamar_gemini),
    ("DeepInfra", _chamar_deepinfra),
    ("OpenRouter", _chamar_openrouter),
    ("DeepSeek", _chamar_deepseek),
    ("HuggingFace", _chamar_hf),
    ("GitHub", _chamar_github_models),
]

def processar_ia_cascade(mensagem, system_override=None, auto_learn=True):
    system_prompt = system_override or SYSTEM_DEFAULT
    log = []
    for nome, fn in CASCADE:
        try:
            txt, info = fn(mensagem, system_prompt)
            log.append(f"{nome}: {info}")
            if txt and str(txt).strip():
                txt = str(txt).strip()
                if auto_learn:
                    salvar_aprendizado_github(f"Q: {mensagem}\nA: {txt}\nPROVIDER: {nome}\nDATA: {datetime.now().isoformat()}", "cascading_ai")
                return txt, nome, log
        except Exception as e:
            log.append(f"{nome}: erro {str(e)[:150]}")
            continue
    return ("⚠️ Nenhuma das APIs em cascata respondeu (Anthropic → Groq → Gemini → DeepInfra → OpenRouter → DeepSeek → HuggingFace → GitHub). "
            "Verifique as chaves na barra lateral. Detalhes:\n- " + "\n- ".join(log)), "NENHUMA", log

# ============================================================
# 4. SPOTIFY — OAuth + controle total
# ============================================================
def spotify_token():
    return _secret("SPOTIFY_ACCESS_TOKEN")

def spotify_headers():
    t = spotify_token()
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"} if t else {}

def get_spotify_devices():
    if not spotify_token():
        return []
    try:
        r = requests.get("https://api.spotify.com/v1/me/player/devices", headers={"Authorization": f"Bearer {spotify_token()}"}, timeout=10)
        if r.status_code == 200:
            return r.json().get("devices", [])
    except Exception:
        pass
    return []

def transfer_spotify_playback(device_id):
    if not spotify_token() or not device_id:
        return False
    try:
        r = requests.put("https://api.spotify.com/v1/me/player", headers=spotify_headers(),
                         json={"device_ids": [device_id], "play": True}, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False

def spotify_control(action, **kw):
    """action: play, pause, next, previous, volume, search_play, queue"""
    if not spotify_token():
        return False, "Sem SPOTIFY_ACCESS_TOKEN. Gere na sidebar (OAuth Spotify)."
    h = {"Authorization": f"Bearer {spotify_token()}"}
    hj = {"Authorization": f"Bearer {spotify_token()}", "Content-Type": "application/json"}
    try:
        if action == "play":
            r = requests.put("https://api.spotify.com/v1/me/player/play", headers=hj, json=kw.get("body", {}), timeout=10)
            return r.status_code in (200, 204), f"play: {r.status_code}"
        if action == "pause":
            r = requests.put("https://api.spotify.com/v1/me/player/pause", headers=h, timeout=10)
            return r.status_code in (200, 204), f"pause: {r.status_code}"
        if action == "next":
            r = requests.post("https://api.spotify.com/v1/me/player/next", headers=h, timeout=10)
            return r.status_code in (200, 204), f"next: {r.status_code}"
        if action == "previous":
            r = requests.post("https://api.spotify.com/v1/me/player/previous", headers=h, timeout=10)
            return r.status_code in (200, 204), f"previous: {r.status_code}"
        if action == "volume":
            v = int(kw.get("volume", 50))
            r = requests.put(f"https://api.spotify.com/v1/me/player/volume?volume_percent={v}", headers=h, timeout=10)
            return r.status_code in (200, 204), f"volume {v}: {r.status_code}"
        if action == "search_play":
            q = kw.get("query", "")
            if not q:
                return False, "Query vazia"
            s = requests.get("https://api.spotify.com/v1/search", headers=h,
                             params={"q": q, "type": "track", "limit": 5}, timeout=10)
            if s.status_code != 200:
                return False, f"busca falhou: {s.status_code} {s.text[:150]}"
            items = s.json().get("tracks", {}).get("items", [])
            if not items:
                return False, "Nada encontrado"
            uri = items[0]["uri"]
            nome = items[0]["name"] + " - " + items[0]["artists"][0]["name"]
            devs = get_spotify_devices()
            body = {"uris": [uri]}
            if devs:
                # toca no primeiro ativo ou primeiro da lista
                ativa = next((d for d in devs if d.get("is_active")), devs[0])
                rp = requests.put("https://api.spotify.com/v1/me/player/play",
                                  headers=hj, params={"device_id": ativa["id"]},
                                  json=body, timeout=10)
            else:
                rp = requests.put("https://api.spotify.com/v1/me/player/play", headers=hj, json=body, timeout=10)
            return rp.status_code in (200, 204), f"Tocando: {nome} ({rp.status_code})"
        if action == "now_playing":
            r = requests.get("https://api.spotify.com/v1/me/player/currently-playing", headers=h, timeout=10)
            if r.status_code == 200 and r.text:
                j = r.json()
                item = j.get("item", {})
                return True, f"🎵 {item.get('name')} — {', '.join(a['name'] for a in item.get('artists', []))}"
            return False, "Nada tocando agora (ou 204)."
    except Exception as e:
        return False, str(e)
    return False, "ação desconhecida"

def spotify_auth_url():
    cid = _secret("SPOTIFY_CLIENT_ID")
    redir = _secret("SPOTIFY_REDIRECT_URI") or "http://localhost:8501"
    if not cid:
        return ""
    scope = "user-read-playback-state user-modify-playback-state user-read-currently-playing streaming"
    p = {"client_id": cid, "response_type": "code", "redirect_uri": redir,
         "scope": scope, "show_dialog": "true"}
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(p)

def spotify_exchange_code(code):
    cid, sec = _secret("SPOTIFY_CLIENT_ID"), _secret("SPOTIFY_CLIENT_SECRET")
    redir = _secret("SPOTIFY_REDIRECT_URI") or "http://localhost:8501"
    r = requests.post("https://accounts.spotify.com/api/token",
                      data={"grant_type": "authorization_code", "code": code, "redirect_uri": redir},
                      auth=(cid, sec), timeout=15)
    return r.status_code, r.text

# ============================================================
# 5. AVATARES 3D ULTRA-REALISTAS (Three.js)
# ============================================================
def gerar_html_avatar_cinematografico(avatar_atual="ZEUS", ultima_resposta="", voz_selecionada=""):
    resp_js = json.dumps(ultima_resposta[:600] if ultima_resposta else "")
    avatar = (avatar_atual or "ZEUS").upper()
    # ---- cena JS por avatar ----
    if avatar == "OLHO":
        cena_js = """
        // ===== OLHO ONISCIENTE =====
        const eyeball = new THREE.Mesh(new THREE.SphereGeometry(1.6, 64, 64),
          new THREE.MeshPhysicalMaterial({color:0xf5f0e6, roughness:0.05, transmission:0.1, clearcoat:1, clearcoatRoughness:0.05}));
        scene.add(eyeball);
        // íris procedural via canvas
        const cnv=document.createElement('canvas'); cnv.width=cnv.height=512;
        const g=cnv.getContext('2d');
        const grad=g.createRadialGradient(256,256,10,256,256,250);
        grad.addColorStop(0,'#000'); grad.addColorStop(0.25,'#000');
        grad.addColorStop(0.32,'#00ffff'); grad.addColorStop(0.55,'#0044aa');
        grad.addColorStop(0.75,'#ff00ff'); grad.addColorStop(1,'#0a0a2a');
        g.fillStyle=grad; g.fillRect(0,0,512,512);
        for(let i=0;i<900;i++){const a=Math.random()*Math.PI*2, r=70+Math.random()*170;
          g.strokeStyle=`hsla(${180+Math.random()*120},100%,${40+Math.random()*30}%,0.5)`;
          g.beginPath(); g.moveTo(256+Math.cos(a)*70,256+Math.sin(a)*70);
          g.lineTo(256+Math.cos(a)*r,256+Math.sin(a)*r); g.stroke();}
        const irisTex=new THREE.CanvasTexture(cnv);
        const iris=new THREE.Mesh(new THREE.CircleGeometry(0.75,64),
          new THREE.MeshBasicMaterial({map:irisTex}));
        iris.position.z=1.52; scene.add(iris);
        const pupil=new THREE.Mesh(new THREE.CircleGeometry(0.3,48),
          new THREE.MeshBasicMaterial({color:0x000000}));
        pupil.position.z=1.54; scene.add(pupil);
        const glow=new THREE.Mesh(new THREE.RingGeometry(0.75,0.95,64),
          new THREE.MeshBasicMaterial({color:0x00ffff,transparent:true,opacity:0.7,side:THREE.DoubleSide}));
        glow.position.z=1.51; scene.add(glow);
        // veias
        for(let i=0;i<24;i++){const pts=[];for(let j=0;j<=8;j++){const a=(i/24)*Math.PI*2+j*0.05;
          pts.push(new THREE.Vector3(Math.cos(a)*(1.2+j*0.08),Math.sin(a)*(1.2+j*0.08),1.0-j*0.06));}
          const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineBasicMaterial({color:0xff2244,transparent:true,opacity:0.55})); scene.add(line);}
        // anel orbital
        const ring=new THREE.Mesh(new THREE.TorusGeometry(2.6,0.03,16,128),
          new THREE.MeshBasicMaterial({color:0xff00ff})); scene.add(ring);
        const ring2=ring.clone(); ring2.rotation.x=Math.PI/2.4; ring2.material=new THREE.MeshBasicMaterial({color:0x00ffff}); scene.add(ring2);
        anim=(t)=>{ const s=1+Math.sin(t*0.0022)*0.06; pupil.scale.set(s,s,1);
          iris.rotation.z=t*0.0002; ring.rotation.z=t*0.0004; ring2.rotation.y=t*0.0005;
          eyeball.rotation.y=Math.sin(t*0.0006)*0.35; eyeball.rotation.x=Math.cos(t*0.0005)*0.2;
          iris.position.x=eyeball.rotation.y*0.35; pupil.position.x=iris.position.x;
          iris.position.y=-eyeball.rotation.x*0.35; pupil.position.y=iris.position.y; };
        """
    elif avatar == "ROBO":
        cena_js = """
        // ===== ROBÔ TITÃ =====
        const metal=new THREE.MeshStandardMaterial({color:0x9aa7bd,metalness:0.95,roughness:0.25});
        const dark=new THREE.MeshStandardMaterial({color:0x11182b,metalness:0.8,roughness:0.4});
        const neonC=new THREE.MeshBasicMaterial({color:0x00ffff});
        const neonM=new THREE.MeshBasicMaterial({color:0xff00ff});
        const head=new THREE.Mesh(new THREE.BoxGeometry(2.2,1.8,2.0),metal); scene.add(head);
        const face=new THREE.Mesh(new THREE.BoxGeometry(1.9,1.1,0.15),dark); face.position.z=1.02; scene.add(face);
        const eyeL=new THREE.Mesh(new THREE.BoxGeometry(0.55,0.22,0.1),neonC); eyeL.position.set(-0.5,0.15,1.12); scene.add(eyeL);
        const eyeR=eyeL.clone(); eyeR.position.x=0.5; scene.add(eyeR);
        const mouth=new THREE.Mesh(new THREE.BoxGeometry(1.0,0.08,0.1),neonM); mouth.position.set(0,-0.35,1.12); scene.add(mouth);
        const ant=new THREE.Mesh(new THREE.CylinderGeometry(0.04,0.04,1.0),metal); ant.position.set(0.8,1.4,0); scene.add(ant);
        const antBall=new THREE.Mesh(new THREE.SphereGeometry(0.12,16,16),neonM); antBall.position.set(0.8,1.95,0); scene.add(antBall);
        const earL=new THREE.Mesh(new THREE.CylinderGeometry(0.3,0.3,0.25,24),dark);
        earL.rotation.z=Math.PI/2; earL.position.set(-1.2,0,0); scene.add(earL);
        const earR=earL.clone(); earR.position.x=1.2; scene.add(earR);
        // grade do chão
        const grid=new THREE.GridHelper(20,30,0x00ffff,0x1a2342); grid.position.y=-2.2; scene.add(grid);
        // partículas
        const pGeo=new THREE.BufferGeometry(); const N=600; const pos=new Float32Array(N*3);
        for(let i=0;i<N;i++){pos[i*3]=(Math.random()-0.5)*16;pos[i*3+1]=Math.random()*8-2;pos[i*3+2]=(Math.random()-0.5)*16;}
        pGeo.setAttribute('position',new THREE.BufferAttribute(pos,3));
        const pts=new THREE.Points(pGeo,new THREE.PointsMaterial({color:0x00ffff,size:0.05,transparent:true,opacity:0.8}));
        scene.add(pts);
        anim=(t)=>{ head.rotation.y=Math.sin(t*0.0005)*0.5; face.position.z=1.02;
          face.rotation.y=head.rotation.y; eyeL.rotation.y=head.rotation.y; eyeR.rotation.y=head.rotation.y; mouth.rotation.y=head.rotation.y;
          const f=0.6+Math.abs(Math.sin(t*0.004))*1.4; eyeL.scale.set(1,f,1); eyeR.scale.set(1,f,1);
          antBall.material.color.setHSL((t*0.0002)%1,1,0.6); pts.rotation.y=t*0.0001; };
        """
    else:
        cena_js = """
        // ===== ZEUS OLÍMPICO ULTRA =====
        const gold=new THREE.MeshStandardMaterial({color:0xd4af37,metalness:1.0,roughness:0.22,emissive:0x332200,emissiveIntensity:0.6});
        const marble=new THREE.MeshStandardMaterial({color:0xe8e4da,metalness:0.1,roughness:0.5});
        const neonC=new THREE.MeshBasicMaterial({color:0x00ffff});
        const neonM=new THREE.MeshBasicMaterial({color:0xff00ff});
        // busto
        const bust=new THREE.Mesh(new THREE.CylinderGeometry(0.9,1.4,1.6,32),marble); bust.position.y=-1.4; scene.add(bust);
        const head=new THREE.Mesh(new THREE.SphereGeometry(1.05,48,48),marble); head.scale.set(0.9,1.15,0.95); scene.add(head);
        // elmo / coroa
        const crown=new THREE.Mesh(new THREE.TorusGeometry(0.95,0.12,16,48),gold); crown.rotation.x=Math.PI/2.15; crown.position.y=0.75; scene.add(crown);
        for(let i=0;i<9;i++){const spike=new THREE.Mesh(new THREE.ConeGeometry(0.1,0.55,8),gold);
          const a=(i/9)*Math.PI*2; spike.position.set(Math.cos(a)*0.95,1.05,Math.sin(a)*0.95); scene.add(spike);}
        // olhos raio
        const eL=new THREE.Mesh(new THREE.SphereGeometry(0.13,16,16),neonC); eL.position.set(-0.32,0.15,0.9); scene.add(eL);
        const eR=eL.clone(); eR.position.x=0.32; scene.add(eR);
        // halo
        const halo=new THREE.Mesh(new THREE.TorusGeometry(1.7,0.035,16,96),neonM); halo.position.y=0.2; scene.add(halo);
        const halo2=new THREE.Mesh(new THREE.TorusGeometry(2.0,0.02,16,96),neonC); halo2.position.y=0.2; scene.add(halo2);
        // raios (linhas zigzag)
        const boltMat=new THREE.LineBasicMaterial({color:0xffe14d});
        for(let i=0;i<6;i++){const pts=[];let x=(Math.random()-0.5)*6,y=3;
          for(let j=0;j<6;j++){pts.push(new THREE.Vector3(x,y,0));x+=(Math.random()-0.5)*0.8;y-=0.7;}
          scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),boltMat));}
        // partículas de energia 1500
        const N=1500, pos=new Float32Array(N*3);
        for(let i=0;i<N;i++){const r=2.5+Math.random()*5,a=Math.random()*Math.PI*2,b=Math.acos(2*Math.random()-1);
          pos[i*3]=r*Math.sin(b)*Math.cos(a);pos[i*3+1]=r*Math.cos(b);pos[i*3+2]=r*Math.sin(b)*Math.sin(a);}
        const pGeo=new THREE.BufferGeometry(); pGeo.setAttribute('position',new THREE.BufferAttribute(pos,3));
        const pts=new THREE.Points(pGeo,new THREE.PointsMaterial({color:0x66ffff,size:0.045,transparent:true,opacity:0.85}));
        scene.add(pts);
        scene.fog=new THREE.FogExp2(0x020205,0.045);
        anim=(t)=>{ head.rotation.y=Math.sin(t*0.0005)*0.45;
          eL.position.x=-0.32+head.rotation.y*0.25; eR.position.x=0.32+head.rotation.y*0.25;
          const p=1+Math.sin(t*0.006)*0.25; eL.scale.set(1,p,1); eR.scale.set(1,p,1);
          halo.rotation.z=t*0.0006; halo2.rotation.z=-t*0.0004; pts.rotation.y=t*0.00012;
          crown.rotation.z=Math.sin(t*0.0008)*0.06; };
        """

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8">
<style>
body,html{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#020205;font-family:'Courier New',monospace;color:#00ffff}}
#three-box{{position:absolute;top:0;left:0;width:100%;height:520px;z-index:1;cursor:grab}}
.ui-overlay{{position:absolute;top:14px;left:16px;z-index:10;pointer-events:none}}
h1{{font-size:1.25rem;margin:0;text-shadow:0 0 12px #00ffff,0 0 25px #ff00ff;letter-spacing:2px}}
p{{font-size:0.78rem;color:#ff00ff;text-shadow:0 0 6px #ff00ff;margin:4px 0 0 0}}
.chat-container{{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);width:95%;max-width:900px;background:rgba(3,3,14,.92);border:1.5px solid #00ffff;box-shadow:0 0 25px rgba(0,255,255,.45),inset 0 0 15px rgba(255,0,255,.25);border-radius:10px;padding:12px;z-index:10;box-sizing:border-box}}
.chat-history{{height:130px;overflow-y:auto;font-size:0.85rem;margin-bottom:8px;display:flex;flex-direction:column;gap:7px;padding-right:6px}}
.message{{padding:7px 11px;border-radius:6px;max-width:88%;line-height:1.4;white-space:pre-wrap}}
.user-msg{{background:rgba(255,0,255,.2);border-left:3px solid #ff00ff;color:#fff;align-self:flex-end}}
.ai-msg{{background:rgba(0,255,255,.12);border-left:3px solid #00ffff;color:#e8f0ff;align-self:flex-start}}
.row{{display:flex;gap:8px}}
input{{flex:1;background:#060913;border:1px solid #00ffff;color:#e8f0ff;border-radius:6px;padding:9px;font-family:inherit}}
button{{background:linear-gradient(135deg,#00ffff,#ff00ff);border:none;border-radius:6px;font-weight:bold;padding:9px 16px;cursor:pointer}}
.hint{{font-size:.7rem;color:#8aa; margin-top:6px}}
</style>
<script type="importmap">{{"imports":{{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}}}</script>
</head>
<body>
<div id="three-box"></div>
<div class="ui-overlay"><h1>⚡ ZEUS 3.0 — {avatar}</h1><p>OLIMPO 3D • ULTRA-REALISTA • ARRASTE P/ GIRAR • SCROLL P/ ZOOM</p></div>
<div class="chat-container">
<div class="chat-history" id="hist"><div class="message ai-msg">⚡ Eu sou o Zeus 3.0 ({avatar}). Fale comigo pelo chat do Streamlit ao lado — respondo em cascata com 8 IAs.</div></div>
<div class="hint">Avatar interativo 3D • O chat oficial fica no painel do Streamlit →</div>
</div>
<script type="module">
import * as THREE from 'three';
import {{OrbitControls}} from 'three/addons/controls/OrbitControls.js';
const box=document.getElementById('three-box');
const scene=new THREE.Scene(); scene.background=new THREE.Color(0x020205);
const camera=new THREE.PerspectiveCamera(55,box.clientWidth/520,0.1,100); camera.position.set(0,0.4,6.2);
const renderer=new THREE.WebGLRenderer({{antialias:true}}); renderer.setSize(box.clientWidth,520); box.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true;
scene.add(new THREE.AmbientLight(0x8899ff,0.9));
const k1=new THREE.DirectionalLight(0xffffff,1.6); k1.position.set(3,5,4); scene.add(k1);
const k2=new THREE.PointLight(0x00ffff,30,30); k2.position.set(-4,2,3); scene.add(k2);
const k3=new THREE.PointLight(0xff00ff,25,30); k3.position.set(4,-1,3); scene.add(k3);
let anim=(t)=>{{}};
{cena_js}
function loop(t){{anim(t);controls.update();renderer.render(scene,camera);requestAnimationFrame(loop);}}
requestAnimationFrame(loop);
window.addEventListener('resize',()=>{{camera.aspect=box.clientWidth/520;camera.updateProjectionMatrix();renderer.setSize(box.clientWidth,520);}});
// voz: lê a última resposta do Zeus
const ULT={resp_js};
try{{ if(ULT && 'speechSynthesis' in window){{ const u=new SpeechSynthesisUtterance(ULT.slice(0,400)); u.lang='pt-BR'; u.rate=1.0; speechSynthesis.cancel(); speechSynthesis.speak(u); }} }}catch(e){{}}
</script>
</body></html>"""
    return html

# ============================================================
# 6. SIDEBAR — chaves + spotify + avatar
# ============================================================
with st.sidebar:
    st.title("⚡ ZEUS 3.0")
    st.caption("IA Autônoma em cascata + Olimpo 3D + Spotify")

    avatar_atual = st.selectbox("🗿 Avatar 3D", ["ZEUS", "OLHO", "ROBO"], index=0)
    voz_on = st.toggle("🔊 Voz do Zeus (TTS navegador)", value=True)

    st.divider()
    st.subheader("🔑 Chaves das 8 IAs (cascata)")
    for prov in PROVIDERS:
        key_name = KEYS[prov]
        ja = bool(_secret(key_name))
        st.text_input(f"{prov} — {key_name}", key=key_name, type="password",
                      placeholder="cole aqui…" if not ja else "•••• salva ✅",
                      help=f"Ou defina env {key_name} / secrets.toml")
    with st.expander("✅ Status das chaves"):
        for prov in PROVIDERS:
            ok = bool(_secret(KEYS[prov]))
            st.markdown(f"{'🟢' if ok else '🔴'} **{prov}** — `{'OK' if ok else 'ausente'}`")

    st.divider()
    st.subheader("🎵 Spotify")
    st.text_input("SPOTIFY_ACCESS_TOKEN", key="SPOTIFY_ACCESS_TOKEN", type="password", placeholder="token OAuth…")
    st.text_input("SPOTIFY_CLIENT_ID", key="SPOTIFY_CLIENT_ID", placeholder="client id…")
    st.text_input("SPOTIFY_CLIENT_SECRET", key="SPOTIFY_CLIENT_SECRET", type="password", placeholder="client secret…")
    st.text_input("SPOTIFY_REDIRECT_URI", key="SPOTIFY_REDIRECT_URI", placeholder="http://localhost:8501")
    url_auth = spotify_auth_url()
    if url_auth:
        st.link_button("🔗 Autorizar no Spotify", url_auth)
    code = st.text_input("Código do Spotify (?code=…)", placeholder="cole o code e Enter")
    if code and st.button("Trocar code por token"):
        sc, txt = spotify_exchange_code(code.strip())
        st.code(f"HTTP {sc}\n{txt[:800]}")
        st.info("Copie o access_token e cole no campo SPOTIFY_ACCESS_TOKEN. (Refresh token: guarde para renovar.)")
    devs = get_spotify_devices()
    if spotify_token():
        if devs:
            st.success(f"{len(devs)} dispositivo(s) encontrado(s)")
            for d in devs:
                c1, c2 = st.columns([3, 1])
                c1.write(f"{'🟢' if d.get('is_active') else '⚪'} {d.get('name')} ({d.get('type')})")
                if c2.button("▶️", key=f"dev_{d.get('id')}"):
                    st.success("Transferido!" if transfer_spotify_playback(d["id"]) else "Falhou")
        else:
            st.warning("Nenhum dispositivo ativo. Abra o Spotify num celular/PC e dê play uma vez.")

    st.divider()
    st.subheader("🧠 Memória / GitHub")
    st.text_input("GITHUB_USER", key="GITHUB_USER", placeholder="seu usuário…")
    st.text_input("GITHUB_REPO", key="GITHUB_REPO", placeholder="nome do repo…")
    auto_learn = st.toggle("Auto-aprender cada resposta", value=True)
    if st.button("📜 Ver memória local"):
        mem = ler_memoria_local(8)
        if not mem:
            st.info("Memória vazia ainda.")
        for n, c in mem:
            with st.expander(n):
                st.text(c[:1500])

# ============================================================
# 7. MAIN — avatar + chat
# ============================================================
st.title("⚡ ZEUS 3.0 — Olimpo 3D & IA em Cascata")
st.caption("Anthropic → Groq → Gemini → DeepInfra → OpenRouter → DeepSeek → HuggingFace → GitHub Models • com auto-aprendizado + Spotify")

if "historico" not in st.session_state:
    st.session_state.historico = []
if "ultima_resposta" not in st.session_state:
    st.session_state.ultima_resposta = ""

col3d, colchat = st.columns([1.15, 1])

with col3d:
    html_avatar = gerar_html_avatar_cinematografico(
        avatar_atual, st.session_state.ultima_resposta if voz_on else "", "")
    st.components.v1.html(html_avatar, height=700, scrolling=False)
    st.caption(f"Avatar ativo: **{avatar_atual}** — arraste para girar, scroll para zoom. 🔊={'on' if voz_on else 'off'}")

with colchat:
    st.subheader("💬 Chat do Zeus (cascata automática)")
    # Spotify quick controls
    with st.expander("🎵 Tocar no Spotify pelo chat", expanded=False):
        q = st.text_input("O que tocar?", placeholder="Ex: Queen Bohemian Rhapsody")
        cc1, cc2, cc3, cc4 = st.columns(4)
        if cc1.button("▶️ Tocar"):
            ok, msg = spotify_control("search_play", query=q or "Zeus Olympia")
            (st.success if ok else st.warning)(msg)
        if cc2.button("⏸️"):
            ok, msg = spotify_control("pause"); st.info(msg)
        if cc3.button("⏭️"):
            ok, msg = spotify_control("next"); st.info(msg)
        if cc4.button("🔊+"):
            ok, msg = spotify_control("volume", volume=80); st.info(msg)
        if st.button("🎵 O que está tocando?"):
            ok, msg = spotify_control("now_playing"); st.info(msg)
        # comando de texto: "toca <musica>"
    for m in st.session_state.historico:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Fale com o Zeus… (ex: toca Queen + me explica buracos negros)")
    if prompt:
        # comando spotify inline
        low = prompt.lower().strip()
        if low.startswith(("toca ", "toque ", "play ")):
            query = prompt.split(" ", 1)[1] if " " in prompt else ""
            ok, msg = spotify_control("search_play", query=query)
            st.session_state.historico.append({"role": "user", "content": prompt})
            st.session_state.historico.append({"role": "assistant", "content": f"🎵 {msg}"})
            st.rerun()
        st.session_state.historico.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("⚡ Zeus consultando a cascata de IAs…"):
                resposta, quem, log = processar_ia_cascade(prompt, auto_learn=auto_learn)
            st.markdown(resposta)
            st.caption(f"Respondido por: **{quem}**")
            with st.expander("🛰️ Log da cascata"):
                st.text("\n".join(log))
        st.session_state.historico.append({"role": "assistant", "content": resposta})
        st.session_state.ultima_resposta = resposta
        # auto-evolução: a cada 6 trocas, pede insight de melhoria
        try:
            n_trocas = len([m for m in st.session_state.historico if m["role"] == "user"])
            if auto_learn and n_trocas > 0 and n_trocas % 6 == 0:
                ins, _, _ = processar_ia_cascade(
                    "Analise as últimas perguntas do usuário e gere 3 linhas de auto-melhoria objetiva "
                    "(o que aprendi, o que farei melhor). Responda em 3 bullets curtos.",
                    auto_learn=False)
                salvar_aprendizado_github(f"AUTO-EVOLUÇÃO após {n_trocas} trocas:\n{ins}", "auto_evolucao")
                st.toast("🧬 Zeus evoluiu sozinho e salvou o aprendizado!")
        except Exception:
            pass
        st.rerun()

    cA, cB = st.columns(2)
    if cA.button("🧹 Limpar chat"):
        st.session_state.historico = []
        st.rerun()
    if cB.button("🧬 Forçar auto-evolução agora"):
        ins, quem, _ = processar_ia_cascade(
            "Gere um parágrafo de auto-melhoria: o que você aprendeu com este usuário e como vai responder melhor daqui em diante.",
            auto_learn=False)
        ok, msg = salvar_aprendizado_github(f"AUTO-EVOLUÇÃO MANUAL:\n{ins}", "auto_evolucao")
        st.success(msg)
        st.markdown(ins)

st.divider()
st.caption("Zeus 3.0 • cascata de 8 IAs • memória local + GitHub • Spotify OAuth + controle • 3 avatares ultra (ZEUS / OLHO / ROBÔ) • feito para o Danilo ⚡")
