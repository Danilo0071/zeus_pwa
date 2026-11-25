import html
import os
import re
import time
import urllib.parse
import requests

REPO_PADRAO = os.getenv("GITHUB_REPO", "Danilo0071/zeus_pwa").strip()
BRANCH_PADRAO = os.getenv("GITHUB_BRANCH", "main").strip()

def ler_github_leve(texto):
    t_lower = texto.strip().lower()
    if not (t_lower.startswith("github") or t_lower.startswith("repo") or t_lower.startswith("issues") or t_lower.startswith("readme")):
        return None
    gh_token = os.getenv("GITHUB_TOKEN", "").strip().strip('"')
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zeus-bot"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    try:
        if "issue" in t_lower:
            url = f"https://api.github.com/repos/{REPO_PADRAO}/issues?state=open&per_page=5"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                itens = [i for i in r.json() if "pull_request" not in i]
                if not itens: return f"No GitHub ({REPO_PADRAO}), nao ha issues abertas agora."
                return "Issues abertas:\n" + "\n".join(f"- #{it.get('number')}: {it.get('title', 'Sem titulo')}" for it in itens[:5])
        if "readme" in t_lower:
            url = f"https://raw.githubusercontent.com/{REPO_PADRAO}/{BRANCH_PADRAO}/README.md"
            r = requests.get(url, headers={"User-Agent": "zeus-bot"}, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return f"README ({REPO_PADRAO}):\n{r.text.strip()[:900]}"
        url = f"https://api.github.com/repos/{REPO_PADRAO}/commits?sha={BRANCH_PADRAO}&per_page=5"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200 and isinstance(r.json(), list):
            commits = r.json()
            if commits:
                linhas = [f"Ultimos commits em {REPO_PADRAO}:"]
                for c in commits[:5]:
                    msg = (c.get("commit", {}).get("message") or "").splitlines()[0][:100]
                    sha = (c.get("sha") or "")[:7]
                    linhas.append(f"- {sha}: {msg}")
                return "\n".join(linhas)
    except Exception:
        pass
    return None

def buscar_contexto_web(consulta):
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip().strip('"')
    if tavily_key:
        try:
            r = requests.post("https://api.tavily.com/search", json={
                "api_key": tavily_key, "query": consulta, "max_results": 3, "include_answer": True
            }, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                partes = []
                if dados.get("answer"): partes.append(dados["answer"])
                for item in dados.get("results", [])[:3]:
                    titulo = item.get("title", ""); trecho = item.get("content", "").strip()[:220]
                    if titulo or trecho: partes.append(f"- {titulo}: {trecho}")
                if partes: return "\n".join(partes)
        except Exception:
            pass
    # Fallback para DuckDuckGo (pode falhar silenciosamente)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        q = urllib.parse.quote_plus(consulta)
        r = requests.get(f"https://html.duckduckgo.com/html/?q={q}", headers=headers, timeout=12)
        if r.status_code == 200:
            titulos = re.findall(r'class="result__a"[^>]>(.?)</a>', r.text, re.S)
            trechos = re.findall(r'class="result__snippet"[^>]>(.?)</a>', r.text, re.S)
            linhas = []
            for t, s in zip(titulos[:3], trechos[:3]):
                t_limpo = html.unescape(re.sub(r"<.*?>", "", t)).strip()
                s_limpo = html.unescape(re.sub(r"<.*?>", "", s)).strip()
                if t_limpo and s_limpo: linhas.append(f"- {t_limpo}: {s_limpo}")
            if linhas: return "\n".join(linhas)
    except Exception:
        pass
    return ""

def chamar_gemini(prompt, api_key):
    if not api_key: return None
    
    # MODELOS ATUALIZADOS PARA CONTAS NOVAS (Linha 3.x)
    # O Google bloqueou o 2.0 e 2.5 para novos usuários. O 3.1/3.5-flash-lite é o novo padrão gratuito.
    modelos = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash" # Mantido como última tentativa, caso a conta tenha sido "abençoada" pelo Google
    ]
    
    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.5, "maxOutputTokens": 1000}}
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                candidatos = r.json().get("candidates", [])
                if candidatos:
                    partes = candidatos[0].get("content", {}).get("parts", [])
                    texto_resp = "".join(p.get("text", "") for p in partes).strip()
                    if texto_resp: return texto_resp
            print(f"Aviso Gemini ({modelo} - {r.status_code}): {r.text[:100]}")
        except Exception as e:
            print(f"Erro Gemini ({modelo}): {e}")
    return None

def gerar_resposta(texto, gemini_key):
    resp_gh = ler_github_leve(texto)
    if resp_gh: return resp_gh

    contexto_web = buscar_contexto_web(texto)

    prompt = (
        "VOCÊ É O ZEUS. Sua única função é responder em PORTUGUÊS DO BRASIL de forma direta e amigável.\n"
        "NUNCA responda em inglês, mesmo que o contexto abaixo esteja em inglês. Traduza tudo.\n"
        f"Usuário perguntou: {texto}\n"
    )
    if contexto_web:
        prompt += f"\nUse estas informações da web para responder (se não fizer sentido, ignore):\n{contexto_web}\n"

    resp_gem = chamar_gemini(prompt, gemini_key)
    if resp_gem: return resp_gem

    # Se a IA falhar, NÃO joga texto em inglês na tela. Avisa o usuário amigavelmente.
    return "Poxa, meus servidores de inteligência artificial estão sobrecarregados agora e não consegui processar sua resposta. Tente me perguntar novamente em alguns minutinhos!"

def enviar_mensagem(token, chat_id, texto):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for inicio in range(0, len(texto), 4000):
        parte = texto[inicio : inicio + 4000]
        try:
            requests.post(url, json={"chat_id": chat_id, "text": parte}, timeout=20)
        except Exception as e:
            print(f"Falha ao enviar: {e}")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"')
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
    chat_id_autorizado = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip('"')

    if not token:
        print("Erro: TELEGRAM_BOT_TOKEN nao configurado.")
        return

    offset = None
    print("Zeus online e escutando...")

    while True:
        try:
            params = {"timeout": 25}
            if offset is not None: params["offset"] = offset
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=35)
            if r.status_code != 200:
                time.sleep(3); continue
            dados = r.json()
            for att in dados.get("result", []):
                offset = att["update_id"] + 1
                msg = att.get("message") or att.get("edited_message") or {}
                texto = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", "")).strip()
                if not texto or not chat_id: continue
                if chat_id_autorizado and chat_id != chat_id_autorizado: continue
                resposta = gerar_resposta(texto, gemini_key)
                enviar_mensagem(token, chat_id, resposta)
        except Exception as e:
            print(f"Erro no loop: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
