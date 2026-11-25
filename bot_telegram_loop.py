import os
import time
import requests


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


def buscar_na_web(consulta):
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not tavily_key:
        return None
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
            resultados = dados.get("results", [])
            if resultados:
                linhas = ["Resultados encontrados na web:"]
                for item in resultados[:3]:
                    titulo = item.get("title", "Sem titulo")
                    resumo = item.get("content", "").strip()[:220]
                    linhas.append(f"- {titulo}: {resumo}")
                return "\n".join(linhas)
    except Exception as e:
        print(f"Erro na busca web: {e}")
    return None


def gerar_resposta(texto, gemini_key):
    prompt = f"Voce e o Zeus, um assistente direto e prestativo. Responda em portugues: {texto}"

    resp = chamar_gemini(prompt, gemini_key, "gemini-3.8-flash")
    if resp:
        return resp

    resp_reserva = chamar_gemini(prompt, gemini_key, "gemini-2.5-flash")
    if resp_reserva:
        return resp_reserva

    resp_web = buscar_na_web(texto)
    if resp_web:
        return resp_web

    return "No momento a IA e a busca estao com instabilidade temporaria. Tente novamente em alguns segundos."


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
