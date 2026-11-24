_RE_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"


def _cmd_email(texto: str) -> str:
    achados = re.findall(_RE_EMAIL, texto)
    para = achados[0] if achados else ""
    if not para and re.search(r"\b(pra|para|pro|ao)\s+(mim|eu|me|meu)\b", texto, re.I):
        para = _secret("EMAIL_TO") or _smtp_cfg()["user"]
    if not para:
        return ("Para quem envio? Ex.: 'envia email para alguem@dominio.com assunto Reunião corpo Confirmada às 15h' "
                "ou 'manda um email pra mim assunto Teste corpo Funcionou'.")
    m_ass = re.search(r"assunto\s*[:\-]?\s*(.+?)(?=\s+(?:corpo|mensagem|texto|dizendo|conte[uú]do)\b|\n|$)", texto, re.I | re.S)
    m_cor = re.search(r"(?:corpo|mensagem|texto|dizendo|conte[uú]do)\s*[:\-]?\s*(.+)$", texto, re.I | re.S)
    assunto = m_ass.group(1).strip(" .,;:") if m_ass else ""
    corpo = m_cor.group(1).strip() if m_cor else ""
    if not corpo and not assunto:
        resto = texto.split(para, 1)[-1].strip(" .,;:-")
        corpo = resto
    if not corpo:
        corpo = assunto or "Mensagem enviada pelo Zeus."
    return enviar_email(para, assunto or "Mensagem do Zeus", corpo)


def _cmd_telegram(texto: str) -> str:
    t = re.sub(r"(?is)^.?\b(telegram|tg)\b\s[:\-]?\s*(?:dizendo|escrevendo|mensagem)?\s*[:\-]?\s*", "", texto, count=1).strip()
    if not t:
        t = re.sub(r"(?i)\b(zeus[,:]?\s*)?(manda|envie|envia|enviar|mande)\b|\b(no|pelo|via)\s+(telegram|tg)\b", "", texto).strip(" .,:-")
    return telegram_enviar(t)


# ============================================================
# MEMÓRIA E CHAT NORMAL
# ============================================================
MEM_KEY = "memoria/fatos.json"


def _mem_ler(forcar=False):
    cache = st.session_state.get("_za_mem")
    if cache and not forcar and time.time() - cache["ts"] < 120:
        return cache["fatos"]
    try:
        fatos = json.loads(b2_get(MEM_KEY).decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception:
        return (cache or {}).get("fatos", [])
    st.session_state["_za_mem"] = {"ts": time.time(), "fatos": fatos}
    return fatos


def mem_ensinar(pergunta: str, resposta: str) -> str:
    try:
        fatos = json.loads(b2_get(MEM_KEY).decode("utf-8"))
    except FileNotFoundError:
        fatos = []
    except Exception as e:
        return f"❌ Não consegui ler a memória (não vou sobrescrever): {_det(e)}"
    fatos.append({"p": pergunta.strip(), "r": resposta.strip(), "ts": _agora().isoformat(timespec="seconds")})
    try:
        b2_put(MEM_KEY, json.dumps(fatos[-500:], ensure_ascii=False, indent=1).encode("utf-8"), "application/json")
    except Exception as e:
        return f"❌ Não consegui gravar a memória: {_det(e)}"
    st.session_state["_za_mem"] = {"ts": time.time(), "fatos": fatos[-500:]}
    return f"🧠 Aprendido e guardado no cofre ({len(fatos)} fatos na memória)."


_STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "que", "eh", "me", "se", "no", "na"}


def _sim(a: str, b: str) -> float:
    a = " ".join(w for w in _norm(a).split() if w not in _STOP)
    b = " ".join(w for w in _norm(b).split() if w not in _STOP)
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return 0.5 * len(ta & tb) / len(ta | tb) + 0.5 * SequenceMatcher(None, a, b).ratio()


def mem_resposta_direta(pergunta: str):
    melhor, s_melhor = None, 0.0
    for f in _mem_ler():
        if f.get("p") == f.get("r"):
            continue
        s = _sim(pergunta, f.get("p", ""))
        if s > s_melhor:
            melhor, s_melhor = f, s
    return melhor["r"] if melhor and s_melhor >= 0.88 else None


def mem_contexto(pergunta: str, n: int = 3) -> str:
    fatos = _mem_ler()
    if not fatos:
        return ""
    pontuados = sorted(((_sim(pergunta, f.get("p", "")), f) for f in fatos), key=lambda x: x[0], reverse=True)
    uteis = [f for s, f in pontuados[:n] if s >= 0.2]
    uteis += [f for f in fatos[-2:] if f not in uteis]
    if not uteis:
        return ""
    return ("[MEMÓRIA ENSINADA PELO CRIADOR — priorize isto sobre conhecimento genérico]\n"
            + "\n".join(f"- {f['p']}" + (f" => {f['r']}" if f["r"] != f["p"] else "") for f in uteis[:n + 2]))


def _cmd_aprender(texto: str) -> str:
    corpo = re.sub(r"(?i)^\s*(zeus[,:\s]+)?(aprenda|aprende|memorize|lembre-se|lembre)\s*(que|:)?\s*", "", texto).strip()
    if not corpo:
        return "O que devo aprender? Ex.: 'aprenda: qual meu time => Palmeiras' ou 'lembre que respondo melhor com exemplos'."
    m = re.split(r"\s*(?:=>|->|=)\s*", corpo, maxsplit=1)
    if len(m) == 2 and m[0] and m[1]:
        return mem_ensinar(m[0], m[1])
    return mem_ensinar(corpo, corpo)


def _cmd_memoria_listar(_t: str) -> str:
    fatos = _mem_ler(forcar=True)
    if not fatos:
        return "Minha memória está vazia. Ensine com: 'aprenda: pergunta => resposta'."
    return f"🧠 Tenho {len(fatos)} fatos. Os últimos:\n" + "\n".join(
        f"- {f['p']}" + (f" => {f['r']}" if f['r'] != f['p'] else "") for f in fatos[-10:])


_RE_PATH = r"([\w.\-]+(?:/[\w.\-]+)+/?|[\w\-]+\.\w{2,5})"


def _path_do_texto(texto: str) -> str:
    for m in re.finditer(_RE_PATH, texto):
        cand = m.group(1)
        if "@" in texto[max(0, m.start() - 1):m.start()] or re.fullmatch(r"\d+\.\d+", cand):
            continue
        return cand.strip(".,;:").lstrip("/")
    return ""


def _cmd_cofre_listar(texto: str) -> str:
    prefixo = _path_do_texto(texto) if "/" in texto else ""
    try:
        itens = b2_list(prefixo, 40)
    except Exception as e:
        return f"❌ Não consegui listar o cofre: {_det(e)}"
    if not itens:
        return f"Cofre vazio{' em ' + prefixo if prefixo else ''} (0 arquivos)."
    return f"📦 Cofre B2 {prefixo or '(raiz)'} — {len(itens)} arquivo(s):\n" + "\n".join(
        f"- {k} ({_bytes_fmt(s)})" for k, s in itens)


def _cmd_cofre_ler(texto: str) -> str:
    chave = _path_do_texto(texto)
    if not chave:
        return "Qual arquivo? Ex.: 'ler dados/bcb/sgs_432_ultimo.json do cofre'."
    dados = None
    for c in [chave] + (["cofre/" + chave] if "/" not in chave else []):
        try:
            dados, chave = b2_get(c), c
            break
        except FileNotFoundError:
            continue
        except Exception as e:
            return f"❌ Não consegui ler {c}: {_det(e)}"
    if dados is None:
        return f"❌ {chave} não existe no cofre. Peça 'lista arquivos do cofre' para ver os nomes."
    txt = dados[:3000].decode("utf-8", "replace")
    return f"📄 {chave} ({_bytes_fmt(len(dados))}):\n{txt}" + ("\n…(cortado)" if len(dados) > 3000 else "")


def _cmd_cofre_apagar(texto: str) -> str:
    chave = _path_do_texto(texto)
    if not chave:
        return "Qual arquivo apagar? Diga o caminho exato. Ex.: 'apaga dados/binance/teste.csv do cofre'."
    if chave in PROTEGIDOS:
        return f"🛡️ {chave} é um arquivo de sistema do Zeus e está protegido contra apagar pelo chat."
    candidatos = [chave] + (["cofre/" + chave] if "/" not in chave else [])
    for c in candidatos:
        try:
            b2_delete(c)
            return f"🗑️ {c} apagado do cofre."
        except FileNotFoundError:
            continue
        except Exception as e:
            return f"❌ Não consegui apagar {c}: {_det(e)}"
    return f"❌ {chave} não existe no cofre (nada foi apagado)."


def _cmd_cofre_salvar(texto: str) -> str:
    m = re.search(r"(?:como|chamad[oa]|nome)\s+([\w.\-/]+)", texto, re.I)
    nome = (m.group(1) if m else "") or _path_do_texto(texto)
    c = re.search(r"(?:dizendo|contendo|conte[uú]do|texto|:)\s*(.+)$", texto, re.I | re.S)
    conteudo = c.group(1).strip() if c else ""
    if not conteudo:
        return "O que devo salvar? Ex.: 'salva no cofre como notas/ideia.txt: texto da ideia'."
    nome = nome.lstrip("/") or f"cofre/nota_{agora():%Y%m%d%H%M%S}.txt"
    if "/" not in nome:
        nome = "cofre/" + nome
    if nome in PROTEGIDOS:
        return f"🛡️ {nome} é protegido."
    try:
        b2_put(nome, conteudo.encode("utf-8"), "text/plain; charset=utf-8")
        return f"✅ Salvo no cofre: {nome} ({_bytes_fmt(len(conteudo.encode('utf-8')))})."
    except Exception as e:
        return f"❌ Não consegui salvar: {_det(e)}"


# ============================================================
# ROTEADOR E CHAT
# ============================================================
def _pos(low, padrao):
    m = re.search(padrao, low)
    return m.start() if m else None


def _rotear(t: str):
    low = _norm(t)
    pergunta = "?" in t or bool(re.match(r"\s*(como|qual|quais|o que|por que|porque|quando|onde|quem)\b", low))
    topo_cofre = r"\b(cofre|bucket|backblaze|blaze\w*|b2|nuvem)\b"
    path = _path_do_texto(t)

    if re.match(r"\s*(zeus[,:\s]+)?(faz\w*\s+(um\s+)?)?(diagnostic\w*|testa\w*|testar|teste\s+(de|do|da)|verifica\w*|checa\w*)\b", low) \
            and re.search(r"\b(conex\w+|e mail|email|telegram|cofre|b2|github|spotify|tudo|sistema|integra\w+)\b", low) \
            and not re.search(r"\b(assunto|corpo|dizendo)\b", low) and len(low.split()) <= 12:
        for foco, pad in (("email", r"\be mail\b|\bemail\b"), ("telegram", r"\btelegram\b"), ("cofre", topo_cofre),
                          ("github", r"\bgithub\b|\brobo\b"), ("spotify", r"\bspotify\b")):
            if re.search(pad, low):
                return diagnostico(foco, enviar_teste=foco in ("email", "telegram"))
        return diagnostico("tudo")

    m = re.match(r"\s*(?:zeus[,:\s]+)?(?:toca|tocar|toque|poe|põe|coloca)\s+(?:a\s+)?(?:musica\s+)?(.+)$", t, re.I)
    if m and not pergunta:
        return sp_tocar(re.sub(r"(?i)\s*(no|pelo)\s+spotify\s*$", "", m.group(1)).strip(" .,!?"))

    if re.search(r"\b(reativ\w+|habilit\w+)\b", low) and re.search(r"\b(robo\w*|action|workflow|agendamento)\b", low):
        return gh_habilitar(_workflow(t))
    if re.search(r"\b(status|situacao|como esta|ultim\w+ (?:execu\w+|rodada)|funcionou)\b", low) and re.search(r"\b(robo\w*|action|workflow)\b", low):
        return gh_status(_workflow(t))
    if re.search(r"\b(aciona\w*|roda|rode|rodar|execut\w+|inicia\w*|liga\w*|chama\w*|ativa\w*|dispara\w*)\b", low) \
            and re.search(r"\b(robo\w*|action|workflow)\b", low):
        return gh_acionar(_workflow(t))

    if re.search(r"\b(apag\w+|delet\w+|remov\w+|exclu\w+)\b", low) and path and (re.search(topo_cofre, low) or "/" in path):
        return _cmd_cofre_apagar(t)
    if re.search(r"\b(salv\w+|guard\w+|grav\w+)\b", low) and re.search(topo_cofre, low) \
            and not re.search(r"\b(binance|banco central|bcb|selic|ipca|cripto\w*)\b", low):
        return _cmd_cofre_salvar(t)

    topico = r"\b(binance|cripto\w*|bitcoin|btc|ethereum|eth|banco central|bcb|selic|ipca|dolar|cdi|mercado|trade|trading|grafic\w+)\b"
    if not pergunta and re.search(topico, low) and re.search(r"\b(baix\w+|colet\w+|pega\w*|traz\w*|atualiz\w+)\b", low):
        return _cmd_dados(t)
    if not pergunta and re.search(r"\b(coleta\w*|colet\w+)\b.*\b(tudo|dados|automatic\w+)\b", low):
        return _cmd_dados(t + " tudo")

    if re.search(r"\b(le|ler|abr\w+|mostr\w+)\b", low) and path and "/" in path and re.search(r"\.\w{2,5}$", path):
        return _cmd_cofre_ler(t)
    if re.search(r"\b(list\w+|mostr\w+|ver|quais|o que (?:tem|ha))\b", low) and re.search(topo_cofre, low):
        return _cmd_cofre_listar(t)

    if re.match(r"\s*(zeus[,:\s]+)?(aprenda|aprende|memorize|lembre-se|lembre)\s*(que|:)", t, re.I):
        return _cmd_aprender(t)
    if re.search(r"\b(o que (?:voce )?aprendeu|sua memoria|mostre a memoria)\b", low):
        return _cmd_memoria_listar(t)

    pt, pe = _pos(low, r"\b(telegram|tg)\b"), _pos(low, r"\b(e mail|email)\b")
    verbo = re.search(r"\b(envi\w+|mand\w+|dispar\w+)\b", low) or low.startswith(("telegram", "email", "e mail"))
    if not pergunta and verbo and (pt is not None or pe is not None):
        if pe is not None and (pt is None or pe < pt):
            return _cmd_email(t)
        return _cmd_telegram(t)

    return None


def _registrar(prompt, resposta):
    g, ss = _G, st.session_state
    sid = ss.session_id
    ss.messages.append({"role": "user", "content": prompt})
    ss.messages.append({"role": "assistant", "content": resposta})
    try:
        g["add_message"](sid, "user", prompt)
        g["add_message"](sid, "assistant", resposta)
    except Exception:
        pass


def _chat_normal(prompt: str) -> str:
    g, ss = _G, st.session_state
    sid = ss.session_id
    escolhido = ss.get("model_sel", "groq")
    ss.messages.append({"role": "user", "content": prompt})

    direta = mem_resposta_direta(prompt)
    if direta:
        ss.messages.append({"role": "assistant", "content": direta})
        g["add_message"](sid, "user", prompt)
        g["add_message"](sid, "assistant", direta)
        ss.last_info = "🧠 Respondido pela memória que você me ensinou"
        return direta

    prompt_llm, fonte_web = g["enriquecer_com_web"](prompt)
    ctx = mem_contexto(prompt)
    base_sys = g.get("SYS_PROMPT", "")
    if ctx:
        g["SYS_PROMPT"] = base_sys + "\n\n" + ctx
    try:
        ok, resp, prov, erros = g["get_llm_response"](sid, prompt_llm, escolhido)
    finally:
        g["SYS_PROMPT"] = base_sys

    if not ok:
        ctx_web, fonte = "", ""
        for _ in range(2):
            ctx_web, fonte = g["web_search"](prompt[:500])
            if ctx_web and ctx_web.strip():
                break
            time.sleep(0.8)
        if ctx_web and ctx_web.strip():
            resp = ("📡 As IAs não responderam. Busquei na internet por conta própria:\n\n"
                    + ctx_web[:3800] + f"\n\n(Fonte: {fonte})")
            ok, prov, fonte_web = True, "Web Search", fonte
        else:
            resp = ("Não consegui contatar as IAs nem obter resultado útil na web agora. "
                    "Tente reformular ou veja o status das chaves no menu lateral.")
            prov = "sem resposta"
            ss.last_info = "⚠️ IAs e web indisponíveis no momento"
    g["add_message"](sid, "user", prompt)
    ss.messages.append({"role": "assistant", "content": resp})
    g["add_message"](sid, "assistant", resp)
    if ok:
        ss.last_info = (f"✅ Respondeu: {prov}" + (f" (falhou: {'; '.join(erros)})" if erros else "")
                        + (" · 🌐 web: " + fonte_web if fonte_web else ""))
    return resp


def processar_prompt(prompt: str) -> str:
    t = (prompt or "").strip()
    if not t:
        return ""
    try:
        resposta = _rotear(t)
    except Exception as e:
        resposta = f"❌ Erro ao executar o comando: {type(e)._name_}: {str(e)[:150]}"
    if resposta is not None:
        _registrar(t, resposta)
        st.session_state.last_info = "⚡ Comando executado"
        return resposta
    return _chat_normal(t)


def _detalhe_erro_corrigido(e: Exception) -> str:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error") or {}
    base = f"{type(e)._name_}: {e}"
    if err.get("Code") or err.get("Message"):
        return f"{base} [{err.get('Code', '')} - {err.get('Message', '')}]"
    return base


def instalar(g: dict):
    global _G
    _G = g
    g["_detalhe_erro"] = _detalhe_erro_corrigido
    original = g.get("add_message")
    if original and not getattr(original, "_za_seguro", False):
        def add_message_seguro(*a, **k):
            try:
                return original(*a, **k)
            except Exception:
                return None
        add_message_seguro._za_seguro = True
        g["add_message"] = add_message_seguro
    return processar_prompt
