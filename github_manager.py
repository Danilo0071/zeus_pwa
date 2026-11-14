"""
github_manager.py - salva as habilidades geradas pelo Zeus no GitHub (arquivo zeus_skills.py).

Secrets necessários (st.secrets ou variáveis de ambiente):
    GITHUB_API_KEY = "github_pat_..."   # token com permissão Contents: Read and write no repositório
    GITHUB_USER    = "seu-usuario"
    GITHUB_REPO    = "nome-do-repositorio"
Opcional:
    GITHUB_BRANCH  = "main"
"""
import ast
import base64
import os

import requests
import streamlit as st

ARQUIVO_SKILLS = "zeus_skills.py"
CABECALHO = '"""Habilidades geradas automaticamente pelo Zeus."""\n'
API = "https://api.github.com"


def _segredo(nome, padrao=""):
    try:
        valor = st.secrets[nome]
        if valor:
            return str(valor).strip()
    except Exception:
        pass
    return os.environ.get(nome, padrao).strip()


def _config():
    token = _segredo("GITHUB_API_KEY")
    user = _segredo("GITHUB_USER")
    repo = _segredo("GITHUB_REPO")
    branch = _segredo("GITHUB_BRANCH")
    faltando = [n for n, v in (("GITHUB_API_KEY", token), ("GITHUB_USER", user), ("GITHUB_REPO", repo)) if not v]
    return token, user, repo, branch, faltando


def _headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(user, repo):
    return f"{API}/repos/{user}/{repo}/contents/{ARQUIVO_SKILLS}"


def ler_do_github():
    """GET: devolve (conteudo_atual | None, sha | None, erro). Arquivo inexistente (404) não é erro."""
    token, user, repo, branch, faltando = _config()
    if faltando:
        return None, None, "Secrets ausentes: " + ", ".join(faltando)
    params = {"ref": branch} if branch else None
    try:
        r = requests.get(_url(user, repo), headers=_headers(token), params=params, timeout=30)
    except Exception as e:
        return None, None, "Falha de conexão com o GitHub: " + str(e)[:120]
    if r.status_code == 404:
        return None, None, ""
    if r.status_code != 200:
        return None, None, f"GitHub GET ({r.status_code}): {r.text[:150]}"
    dados = r.json()
    conteudo = base64.b64decode(dados.get("content", "")).decode("utf-8", errors="replace")
    return conteudo, dados.get("sha"), ""


def _gravar_local(conteudo):
    """Cópia local (melhor esforço) para o app poder recarregar a habilidade sem esperar o redeploy."""
    try:
        caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ARQUIVO_SKILLS)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(conteudo)
    except Exception:
        pass


def salvar_no_github(codigo_novo, mensagem="Zeus: nova habilidade"):
    """
    Anexa o código novo ao zeus_skills.py do repositório.
    GET para pegar o SHA e o conteúdo atual, PUT para atualizar.
    Retorna (ok: bool, mensagem: str).
    """
    token, user, repo, branch, faltando = _config()
    if faltando:
        return False, "Secrets ausentes: " + ", ".join(faltando)

    for tentativa in range(2):  # 2ª volta só se houver conflito de SHA (409/422)
        atual, sha, erro = ler_do_github()
        if erro:
            return False, erro
        combinado = (atual or CABECALHO).rstrip() + "\n\n\n" + codigo_novo.strip() + "\n"
        try:
            ast.parse(combinado)
        except SyntaxError as e:
            return False, f"O arquivo combinado ficaria inválido (linha {e.lineno}): {e.msg}"

        corpo = {
            "message": mensagem[:200],
            "content": base64.b64encode(combinado.encode("utf-8")).decode("ascii"),
        }
        if sha:
            corpo["sha"] = sha
        if branch:
            corpo["branch"] = branch
        try:
            r = requests.put(_url(user, repo), headers=_headers(token), json=corpo, timeout=30)
        except Exception as e:
            return False, "Falha de conexão com o GitHub: " + str(e)[:120]
        if r.status_code in (200, 201):
            _gravar_local(combinado)
            return True, "Salvo em " + ARQUIVO_SKILLS
        if r.status_code in (409, 422) and tentativa == 0:
            continue
        dica = " (o token precisa de Contents: Read and write)" if r.status_code in (401, 403, 404) else ""
        return False, f"GitHub PUT ({r.status_code}): {r.text[:150]}{dica}"
    return False, "Conflito ao salvar no GitHub. Tente novamente."
