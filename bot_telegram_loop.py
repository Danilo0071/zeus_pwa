import html
import os
import re
import time
import urllib.parse
import requests

REPO_PADRAO = os.getenv("GITHUB_REPO", "Danilo0071/zeus_pwa").strip()
BRANCH_PADRAO = os.getenv("GITHUB_BRANCH", "main").strip()


def formatar_zeus(conteudo):
    return f"Zeus aqui:\n\n{conteudo.strip()}"


def ler_github_leve(texto):
    t = texto.strip()
    t_lower = t.lower()
    if not (
        t_lower.startswith("github")
        or t_lower.startswith("repo")
        or t_lower.startswith("issues")
        or t_lower.startswith("readme")
    ):
        return None

    gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zeus-bot"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    try:
        if "issue" in t_lower:
            url = f"https://api.github.com/repos/{REPO_PADRAO}/issues?state=open&per_page=5"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                itens = [i for i in r.json() if "pull_request" not in i]
                if not itens:
                    return f"No GitHub ({REPO_PADRAO}), nao ha issues abertas agora."
                linhas = [f"Issues abertas em {REPO_PADRAO}:"]
                for it in itens[:5]:
                    linhas.append(f"- #{it.get('number')}: {it.get('title', 'Sem titulo')}")
                return "\n".join(linhas)

        if "readme" in t_lower:
            url = f"https://raw.githubusercontent.com/{REPO_PADRAO}/{BRANCH_PADRAO}/README.md"
            r = requests.get(url, headers={"User-Agent": "zeus-bot"}, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return f"README de {REPO_PADRAO}:\n\n{r.text.strip()[:900]}"

        url = f"https://api.github.com/repos/{REPO_PADRAO}/commits?sha={BRANCH_PADRAO}&per_page=5"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            commits = r.json()
            if isinstance(commits, list) and commits:
                linhas = [f"Ultimos commits em {REPO_PADRAO} ({BRANCH_PADRAO}):"]
                for c in commits[:5]:
                    msg = (c.get("commit", {}).get("message") or "").splitlines()[0][:100]
                    sha = (c.get("sha") or "")[:7]
                    linhas.append(f"- {sha}: {msg}")
                return "\n".join(linhas)
    except Exception as e:
        print(f"Erro na leitura leve do GitHub: {e}")
    return None


def buscar_na_web(consulta):
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": consulta, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            dados = r.json()
            resumo = (dados.get("AbstractText") or dados.get("Answer") or "").strip()
            if resumo:
                return f"Achei isso na web:\n\n{resumo}"
    except Exception as e:
        print(f"Aviso DDG JSON: {e}")

    try:
        q = urllib.parse.quote_plus(consulta)
        r = requests.get(
            f"https://html.duckduckgo.com/html/?q={q}",
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            titulos = re.findall(r'class="result__a"[^>]>(.?)</a>', r.text, re.S)
            trechos = re.findall(r'class="result__snippet"[^>]>(.?)</a>', r.text, re.S)
            linhas = []
            for t, s in zip(titulos[:3], trechos[:3]):
                t_limpo = html.unescape(re.sub(r"<.*?>", "", t)).strip()
                s_limpo = html.unescape(re.sub(r"<.*?>", "", s)).strip()
                if t_limpo and s_limpo:
                    linhas.append(f"- {t_limpo}: {s_limpo}")
            if linhas:
                return "Resultados na web:\n\n" + "\n".join(linhas)
    except Exception as e:
        print(f"Erro na busca web DDG: {e}")

    return None


def chamar_gemini(prompt, api_key, modelo):
    if not api_key:
        return None
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for _ in range(2):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                dados = r.json()
                candidatos = dados.get("candidates", [])
                if candidatos:
                    partes = candidatos[0].get("content", {}).get("parts", [])
                    texto_resp = "".join(p.get("text", "") for p in partes).strip()
                    if texto_resp:
                        return texto_resp
            elif r.status_code == 503:
                time.sleep(2)
                continue
            print(f"Aviso Gemini ({modelo} - {r.status_code}): {r.text[:200]}")
            return None
        except Exception as e:
            print(f"Erro na requisicao Gemini ({modelo}): {e}")
            time.sleep(1)
    return None


def chamar_outra_chave(prompt, consulta):
    grok_key = os.getenv("GROK_API_KEY", "").strip()
    if grok_key:
        try:
            r = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {grok_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-beta",
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            if r.status_code == 200:
                conteudo = (
                    r.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if conteudo:
                    return conteudo
            print(f"Aviso Grok ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            print(f"Erro na requisicao Grok: {e}")

    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": consulta,
                    "max_results": 3,
                    "include_answer": True,
                },
                timeout=20,
            )
            if r.status_code == 200:
                dados = r.json()
                if dados.get("answer"):
                    return f"Encontrei isso na web:\n\n{dados['answer']}"
        except Exception as e:
            print(f"Erro na busca Tavily: {e}")

    return None


def gerar_resposta(texto, gemini_key):
    # 1. Primeiro leitura leve do GitHub e busca na web (sem depender de LLM)
    resp_gh = ler_github_leve(texto)
    if resp_gh:
        return formatar_zeus(resp_gh)

    resp_web = buscar_na_web(texto)
    if resp_web:
        return formatar_zeus(resp_web)

    prompt = (
        "Voce e o Zeus, um assistente direto, parceiro e prestativo, respondendo em portugues "
        "com o mesmo estilo claro e objetivo do app Zeus. Responda: " + texto
    )

    # 2. Depois tenta o Gemini
    resp_gem = chamar_gemini(prompt, gemini_key, "gemini-3.8-flash")
    if resp_gem:
        return formatar_zeus(resp_gem)

    resp_gem_2 = chamar_gemini(prompt, gemini_key, "gemini-2.5-flash")
    if resp_gem_2:
        return formatar_zeus(resp_gem_2)

    # 3. Por ultimo tenta a outra chave configurada (Grok / Tavily)
    resp_extra = chamar_outra_chave(prompt, texto)
    if resp_extra:
        return formatar_zeus(resp_extra)

    return formatar_zeus(
        "Nao consegui responder agora porque a busca e as IAs estao com instabilidade temporaria. Tente de novo em instantes."
    )


def enviar_mensagem(token, chat_id, texto):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for inicio in range(0, len(texto), 4000):
        parte = texto[inicio : inicio + 4000]
        try:
            r = requests.post(
                url,
                json={"chat_id": chat_id, "text": parte},
                timeout=20,
            )
            if r.status_code == 200:
                print(f"Envio para {chat_id}: OK")
            else:
                print(f"Erro ao enviar para {chat_id} ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            print(f"Falha de rede ao enviar para {chat_id}: {e}")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    chat_id_autorizado = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        print("Erro: TELEGRAM_BOT_TOKEN nao configurado.")
        return

    offset = None
    print("Zeus escutando mensagens em tempo real...")

    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset

            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params=params,
                timeout=35,
            )
            if r.status_code != 200:
                print(f"Aviso getUpdates ({r.status_code}): {r.text[:200]}")
                time.sleep(3)
                continue

            dados = r.json()
            for att in dados.get("result", []):
                offset = att["update_id"] + 1
                msg = att.get("message") or att.get("edited_message") or {}
                texto = (msg.get("text") or "").strip()
                chat_id = str(msg.get("chat", {}).get("id", "")).strip()

                if not texto or not chat_id:
                    continue

                if chat_id_autorizado and chat_id != chat_id_autorizado:
                    print(f"Ignorando chat_id nao autorizado: {chat_id}")
                    continue

                resposta = gerar_resposta(texto, gemini_key)
                enviar_mensagem(token, chat_id, resposta)

        except Exception as e:
            print(f"Erro no loop principal: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
