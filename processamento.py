from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import urlparse

import requests

STATUS_DOWNLOAD_VALIDOS = {
    "BAIXADO_COM_SUCESSO",
    "JA_EXISTIA_VALIDADO",
    "SEM_LINK_PDF",
    "ACESSO_RESTRITO",
    "HTTP_403",
    "HTTP_404",
    "RATE_LIMIT_429",
    "ERRO_TIMEOUT",
    "ERRO_CONEXAO",
    "RESPOSTA_NAO_PDF",
    "PDF_INVALIDO",
    "ERRO_GRAVACAO",
    "DOWNLOAD_CANCELADO",
    "DOWNLOAD_NAO_SOLICITADO",
    "FALHA_APOS_TENTATIVAS",
}


class ControleExecucao:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pausado = threading.Event()
        self._pausado.set()
        self._cancelado = False

    def resetar(self) -> None:
        with self._lock:
            self._cancelado = False
            self._pausado.set()

    def pausar(self) -> None:
        self._pausado.clear()

    def retomar(self) -> None:
        self._pausado.set()

    def cancelar(self) -> None:
        with self._lock:
            self._cancelado = True
        self._pausado.set()

    def esta_pausado(self) -> bool:
        return not self._pausado.is_set()

    def esta_cancelado(self) -> bool:
        with self._lock:
            return self._cancelado

    def aguardar_se_pausado(self) -> None:
        while not self._pausado.is_set():
            if self._cancelado:
                break
            self._pausado.wait(timeout=0.2)


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    txt = unicodedata.normalize("NFKD", str(texto)).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"\s+", " ", txt.lower()).strip()


def limpar_nome_arquivo_windows(texto: str, max_len: int = 80) -> str:
    txt = unicodedata.normalize("NFKD", str(texto or "")).encode("ASCII", "ignore").decode("ASCII")
    txt = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" .")
    if not txt:
        return ""
    if len(txt) > max_len:
        txt = txt[:max_len].rstrip(" .")
    return txt


def extrair_primeiro_autor(autores_str: str) -> str:
    if not autores_str:
        return "Sem_Autor"
    primeiro = re.split(r";|\band\b|\be\b", autores_str, maxsplit=1, flags=re.I)[0].strip()
    if "," in primeiro:
        sobrenome = primeiro.split(",", 1)[0].strip()
    else:
        partes = primeiro.split()
        sobrenome = partes[-1].strip() if partes else "Sem_Autor"
    limpo = limpar_nome_arquivo_windows(sobrenome, max_len=35)
    return limpo or "Sem_Autor"


def formatar_nome_pdf(registro: dict[str, Any], usados: set[str] | None = None) -> str:
    autor = extrair_primeiro_autor(registro.get("autores", ""))
    ano_raw = re.findall(r"\d{4}", str(registro.get("ano") or ""))
    ano = ano_raw[0] if ano_raw else "S_D"
    titulo = limpar_nome_arquivo_windows(registro.get("titulo") or "Sem_Titulo", max_len=75) or "Sem_Titulo"
    base = f"{autor} ({ano}) - {titulo}"
    nome_final = f"{base}.pdf"

    if usados is not None:
        contador = 2
        while nome_final.lower() in usados:
            nome_final = f"{base} ({contador}).pdf"
            contador += 1
        usados.add(nome_final.lower())
    return nome_final


def carregar_checkpoint(caminho: str) -> dict[str, Any]:
    if not caminho or not os.path.exists(caminho):
        return {"consultas_concluidas": [], "registros_brutos": []}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "consultas_concluidas": list(data.get("consultas_concluidas") or []),
                "registros_brutos": list(data.get("registros_brutos") or []),
            }
    except Exception:
        pass
    return {"consultas_concluidas": [], "registros_brutos": []}


def salvar_checkpoint(caminho: str, consultas_concluidas: list[str], registros_brutos: list[dict[str, Any]]) -> None:
    tmp_path = f"{caminho}.part"
    payload = {
        "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "consultas_concluidas": consultas_concluidas,
        "registros_brutos": registros_brutos,
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, caminho)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _normalizar_id_externo(ext_id: str) -> str:
    val = normalizar_texto(ext_id or "")
    val = re.sub(r"^(pmcid:|pmid:|https?://(www\.)?openalex\.org/|https?://arxiv\.org/(abs|pdf)/)", "", val)
    return val.strip()


def deduplicar_com_proveniencia(brutos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    todos: list[dict[str, Any]] = []
    unicos: list[dict[str, Any]] = []

    indice_doi: dict[str, dict[str, Any]] = {}
    indice_ext: dict[str, dict[str, Any]] = {}
    indice_titulo: dict[str, dict[str, Any]] = {}
    titulos_norm_lista: list[tuple[str, dict[str, Any]]] = []

    for idx, item in enumerate(brutos, start=1):
        reg = dict(item)
        reg["id_interno"] = f"REG-{idx:05d}"
        reg["plataforma_inicial"] = reg.get("plataforma", "")
        reg["string_busca_inicial"] = reg.get("string_busca", "")
        reg["plataformas_origem"] = [reg.get("plataforma", "")] if reg.get("plataforma") else []
        reg["strings_origem"] = [reg.get("string_busca", "")] if reg.get("string_busca") else []
        reg["identificadores_coletados"] = [x for x in [reg.get("doi"), reg.get("identificador_externo")] if x]

        doi_norm = (reg.get("doi") or "").strip().lower()
        ext_norm = _normalizar_id_externo(reg.get("identificador_externo") or "")
        tit_norm = normalizar_texto(reg.get("titulo") or "")

        mestre: dict[str, Any] | None = None
        criterio_match = ""

        if doi_norm and doi_norm in indice_doi:
            mestre = indice_doi[doi_norm]
            criterio_match = "DOI_NORMALIZADO"

        if mestre is None and ext_norm and len(ext_norm) >= 4 and ext_norm in indice_ext:
            mestre = indice_ext[ext_norm]
            criterio_match = "IDENTIFICADOR_EXTERNO"

        if mestre is None and tit_norm and len(tit_norm) >= 8 and tit_norm in indice_titulo:
            cand = indice_titulo[tit_norm]
            ano_a = str(cand.get("ano") or "")
            ano_b = str(reg.get("ano") or "")
            if not ano_a or not ano_b or ano_a == ano_b:
                mestre = cand
                criterio_match = "TITULO_NORMALIZADO"

        if mestre is None and tit_norm and len(tit_norm) >= 15:
            ano_b = str(reg.get("ano") or "")
            for t_prev, cand in titulos_norm_lista:
                ano_a = str(cand.get("ano") or "")
                if ano_a and ano_b and ano_a != ano_b:
                    continue
                ratio = SequenceMatcher(None, tit_norm, t_prev).ratio()
                if ratio >= 0.94:
                    mestre = cand
                    criterio_match = f"TITULO_APROXIMADO({ratio:.2f})"
                    break

        if mestre is not None:
            reg["status_dedup"] = "DUPLICADO"
            reg["id_registro_original"] = mestre["id_interno"]
            reg["criterio_dedup"] = criterio_match

            if reg.get("plataforma") and reg["plataforma"] not in mestre["plataformas_origem"]:
                mestre["plataformas_origem"].append(reg["plataforma"])
            if reg.get("string_busca") and reg["string_busca"] not in mestre["strings_origem"]:
                mestre["strings_origem"].append(reg["string_busca"])
            for ident in reg.get("identificadores_coletados", []):
                if ident not in mestre["identificadores_coletados"]:
                    mestre["identificadores_coletados"].append(ident)
            if not mestre.get("pdf_url") and reg.get("pdf_url"):
                mestre["pdf_url"] = reg["pdf_url"]
            if not mestre.get("resumo") and reg.get("resumo"):
                mestre["resumo"] = reg["resumo"]
            if not mestre.get("periodico") and reg.get("periodico"):
                mestre["periodico"] = reg["periodico"]
            if not mestre.get("issn") and reg.get("issn"):
                mestre["issn"] = reg["issn"]
        else:
            reg["status_dedup"] = "UNICO"
            reg["id_registro_original"] = reg["id_interno"]
            reg["criterio_dedup"] = "ORIGINAL"
            unicos.append(reg)

            if doi_norm:
                indice_doi[doi_norm] = reg
            if ext_norm and len(ext_norm) >= 4:
                indice_ext[ext_norm] = reg
            if tit_norm and len(tit_norm) >= 8:
                indice_titulo[tit_norm] = reg
            if tit_norm and len(tit_norm) >= 15:
                titulos_norm_lista.append((tit_norm, reg))

        todos.append(reg)

    for u in unicos:
        u["plataformas_consolidadas"] = "; ".join(u["plataformas_origem"])
        u["strings_consolidadas"] = " | ".join(u["strings_origem"])

    for r in todos:
        r["plataformas_consolidadas"] = "; ".join(r.get("plataformas_origem") or [r.get("plataforma", "")])
        r["strings_consolidadas"] = " | ".join(r.get("strings_origem") or [r.get("string_busca", "")])

    return todos, unicos


def filtrar_registros(
    todos_registros: list[dict[str, Any]],
    unicos_registros: list[dict[str, Any]],
    termos_incl: list[str],
    termos_excl: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    aprovados: list[dict[str, Any]] = []
    excluidos: list[dict[str, Any]] = []
    ignorados: list[dict[str, Any]] = []

    incl_limpos = [t.strip() for t in (termos_incl or []) if t.strip()]
    excl_limpos = [t.strip() for t in (termos_excl or []) if t.strip()]

    unicos_ids = {u["id_interno"] for u in unicos_registros}

    for r in todos_registros:
        if r["id_interno"] not in unicos_ids:
            r["situacao_triagem"] = "IGNORADO_DUPLICADO"
            r["motivo_triagem"] = f"Duplicado de {r.get('id_registro_original')} ({r.get('criterio_dedup')})"
            r["termo_responsavel"] = r.get("criterio_dedup", "")
            ignorados.append(r)
            continue

        if not (r.get("titulo") or "").strip():
            r["situacao_triagem"] = "EXCLUIDO"
            r["motivo_triagem"] = "Registro sem título válido"
            r["termo_responsavel"] = "TITULO_AUSENTE"
            excluidos.append(r)
            continue

        corpo = normalizar_texto(f"{r.get('titulo', '')} {r.get('resumo', '')}")

        excluido_por_termo = False
        for termo in excl_limpos:
            tn = normalizar_texto(termo)
            if tn and re.search(rf"\b{re.escape(tn)}\b", corpo):
                r["situacao_triagem"] = "EXCLUIDO"
                r["motivo_triagem"] = f"Contém termo de exclusão: '{termo}'"
                r["termo_responsavel"] = termo
                excluidos.append(r)
                excluido_por_termo = True
                break

        if excluido_por_termo:
            continue

        ausente_obrigatorio = False
        for termo in incl_limpos:
            tn = normalizar_texto(termo)
            if tn and not re.search(rf"\b{re.escape(tn)}\b", corpo):
                r["situacao_triagem"] = "EXCLUIDO"
                r["motivo_triagem"] = f"Ausência do termo obrigatório: '{termo}'"
                r["termo_responsavel"] = termo
                excluidos.append(r)
                ausente_obrigatorio = True
                break

        if ausente_obrigatorio:
            continue

        r["situacao_triagem"] = "APROVADO"
        r["motivo_triagem"] = "Aprovado nos critérios determinísticos"
        r["termo_responsavel"] = ""
        aprovados.append(r)

    return aprovados, excluidos, ignorados


def _validar_url_pdf(url: str) -> tuple[bool, str]:
    if not url or not isinstance(url, str):
        return False, "URL de PDF ausente ou vazia."
    u = url.strip()
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, f"Esquema ou host inválido na URL: '{u}'"
    return True, ""


def _calcular_sha256_arquivo(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validar_assinatura_pdf(caminho: str) -> tuple[bool, str]:
    if not os.path.exists(caminho):
        return False, "Arquivo não encontrado em disco."
    tamanho = os.path.getsize(caminho)
    if tamanho < 1024:
        return False, f"Arquivo muito pequeno ({tamanho} bytes) para ser um PDF válido."
    with open(caminho, "rb") as f:
        cabecalho = f.read(1024)
    if b"%PDF" not in cabecalho:
        return False, "Assinatura binária %PDF não encontrada no cabeçalho."
    return True, ""


def baixar_pdfs_seguro(
    registros: list[dict[str, Any]],
    pasta_pdfs: str,
    email: str,
    timeout: int = 30,
    max_tentativas: int = 3,
    controle: ControleExecucao | None = None,
    on_log: Callable[[str], None] | None = None,
    on_progresso: Callable[[int, int, str], None] | None = None,
    on_metrica_pdf: Callable[[int], None] | None = None,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    os.makedirs(pasta_pdfs, exist_ok=True)
    baixados_ok = 0
    ja_existiam_ok = 0
    falhas = 0
    historico: list[dict[str, Any]] = []
    nomes_usados: set[str] = set()
    hashes_baixados: dict[str, str] = {}
    dois_baixados: dict[str, str] = {}

    for existente in os.listdir(pasta_pdfs):
        if existente.lower().endswith(".pdf") and not existente.lower().endswith(".part"):
            nomes_usados.add(existente.lower())
            full_ex = os.path.join(pasta_pdfs, existente)
            ok_ex, _ = _validar_assinatura_pdf(full_ex)
            if ok_ex:
                hashes_baixados[_calcular_sha256_arquivo(full_ex)] = existente

    session = requests.Session()
    session.headers.update({
        "User-Agent": f"PesquisanatorXP/3.0 (mailto:{email})",
        "Accept": "application/pdf, */*",
    })

    total = len(registros)
    try:
        for idx, reg in enumerate(registros, start=1):
            if controle and controle.esta_cancelado():
                reg["status_download"] = "DOWNLOAD_CANCELADO"
                reg["mensagem_download"] = "Download cancelado pelo usuário."
                reg["tentativas_download"] = 0
                reg["data_tentativa_download"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": reg.get("doi", ""),
                    "url_pdf": reg.get("pdf_url", ""),
                    "tentativa": 0,
                    "data_hora": reg["data_tentativa_download"],
                    "status_download": "DOWNLOAD_CANCELADO",
                    "arquivo_pdf": "",
                    "tamanho_bytes": 0,
                    "sha256": "",
                    "mensagem_erro": reg["mensagem_download"],
                })
                continue

            if controle:
                controle.aguardar_se_pausado()

            if on_progresso:
                on_progresso(idx, total, reg.get("titulo", "")[:40])

            url = (reg.get("pdf_url") or "").strip()
            doi = (reg.get("doi") or "").strip().lower()
            agora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not url:
                reg["status_download"] = "SEM_LINK_PDF"
                reg["mensagem_download"] = "Nenhum link de PDF informado pela plataforma."
                reg["tentativas_download"] = 0
                reg["data_tentativa_download"] = agora_str
                reg["arquivo_pdf"] = ""
                reg["tamanho_pdf_bytes"] = 0
                reg["sha256_pdf"] = ""
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": "",
                    "tentativa": 0,
                    "data_hora": agora_str,
                    "status_download": "SEM_LINK_PDF",
                    "arquivo_pdf": "",
                    "tamanho_bytes": 0,
                    "sha256": "",
                    "mensagem_erro": reg["mensagem_download"],
                })
                continue

            valido_url, msg_url = _validar_url_pdf(url)
            if not valido_url:
                falhas += 1
                reg["status_download"] = "RESPOSTA_NAO_PDF"
                reg["mensagem_download"] = msg_url
                reg["tentativas_download"] = 0
                reg["data_tentativa_download"] = agora_str
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": url,
                    "tentativa": 0,
                    "data_hora": agora_str,
                    "status_download": "RESPOSTA_NAO_PDF",
                    "arquivo_pdf": "",
                    "tamanho_bytes": 0,
                    "sha256": "",
                    "mensagem_erro": msg_url,
                })
                continue

            if doi and doi in dois_baixados:
                arq_existente = dois_baixados[doi]
                caminho_existente = os.path.join(pasta_pdfs, arq_existente)
                tam_ex = os.path.getsize(caminho_existente) if os.path.exists(caminho_existente) else 0
                sha_ex = _calcular_sha256_arquivo(caminho_existente) if os.path.exists(caminho_existente) else ""
                ja_existiam_ok += 1
                reg["status_download"] = "JA_EXISTIA_VALIDADO"
                reg["mensagem_download"] = f"PDF já obtido e validado para DOI {doi}."
                reg["arquivo_pdf"] = arq_existente
                reg["tamanho_pdf_bytes"] = tam_ex
                reg["sha256_pdf"] = sha_ex
                reg["tentativas_download"] = 0
                reg["data_tentativa_download"] = agora_str
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": url,
                    "tentativa": 0,
                    "data_hora": agora_str,
                    "status_download": "JA_EXISTIA_VALIDADO",
                    "arquivo_pdf": arq_existente,
                    "tamanho_bytes": tam_ex,
                    "sha256": sha_ex,
                    "mensagem_erro": "",
                })
                continue

            nome_destino = formatar_nome_pdf(reg, nomes_usados)
            caminho_final = os.path.join(pasta_pdfs, nome_destino)
            caminho_tmp = f"{caminho_final}.part"

            status_final = "FALHA_APOS_TENTATIVAS"
            msg_final = "Limite de tentativas excedido."
            tamanho_final = 0
            sha_final = ""
            tentativa_usada = 0

            for tentativa in range(1, max(1, max_tentativas) + 1):
                tentativa_usada = tentativa
                ts_tent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if controle and controle.esta_cancelado():
                    status_final = "DOWNLOAD_CANCELADO"
                    msg_final = "Download cancelado durante transferência."
                    if os.path.exists(caminho_tmp):
                        try:
                            os.remove(caminho_tmp)
                        except Exception:
                            pass
                    break

                try:
                    with session.get(url, timeout=timeout, allow_redirects=True, stream=True) as resp:
                        codigo = resp.status_code
                        if codigo == 429:
                            status_final = "RATE_LIMIT_429"
                            msg_final = f"Servidor retornou HTTP 429 em {url}"
                        elif codigo == 403:
                            status_final = "HTTP_403"
                            msg_final = f"Acesso proibido (HTTP 403) em {url}"
                        elif codigo == 404:
                            status_final = "HTTP_404"
                            msg_final = f"Recurso não encontrado (HTTP 404) em {url}"
                        elif codigo != 200:
                            status_final = "ACESSO_RESTRITO" if codigo in {401, 402, 451} else "RESPOSTA_NAO_PDF"
                            msg_final = f"Status HTTP {codigo} ao acessar {url}"
                        else:
                            ctype = (resp.headers.get("Content-Type") or "").lower()
                            try:
                                with open(caminho_tmp, "wb") as f_out:
                                    for bloco in resp.iter_content(chunk_size=16384):
                                        if controle and controle.esta_cancelado():
                                            raise InterruptedError("Download cancelado.")
                                        if bloco:
                                            f_out.write(bloco)
                            except InterruptedError:
                                status_final = "DOWNLOAD_CANCELADO"
                                msg_final = "Download interrompido por cancelamento."
                                if os.path.exists(caminho_tmp):
                                    os.remove(caminho_tmp)
                                break
                            except OSError as io_exc:
                                status_final = "ERRO_GRAVACAO"
                                msg_final = f"Falha de gravação em disco: {io_exc}"
                                if os.path.exists(caminho_tmp):
                                    try:
                                        os.remove(caminho_tmp)
                                    except Exception:
                                        pass
                                break

                            ok_pdf, motivo_pdf = _validar_assinatura_pdf(caminho_tmp)
                            if not ok_pdf:
                                if os.path.exists(caminho_tmp):
                                    os.remove(caminho_tmp)
                                if "pdf" not in ctype and "octet-stream" not in ctype:
                                    status_final = "RESPOSTA_NAO_PDF"
                                    msg_final = f"Conteúdo não é PDF (Content-Type: '{ctype or 'desconhecido'}'). {motivo_pdf}"
                                else:
                                    status_final = "PDF_INVALIDO"
                                    msg_final = motivo_pdf
                            else:
                                sha_cand = _calcular_sha256_arquivo(caminho_tmp)
                                if sha_cand in hashes_baixados:
                                    os.remove(caminho_tmp)
                                    arq_dup = hashes_baixados[sha_cand]
                                    status_final = "JA_EXISTIA_VALIDADO"
                                    msg_final = f"Conteúdo idêntico (SHA-256) já salvo em '{arq_dup}'."
                                    nome_destino = arq_dup
                                    tamanho_final = os.path.getsize(os.path.join(pasta_pdfs, arq_dup))
                                    sha_final = sha_cand
                                    break

                                if os.path.exists(caminho_final):
                                    os.remove(caminho_tmp)
                                    status_final = "JA_EXISTIA_VALIDADO"
                                    msg_final = "Arquivo já existente na pasta de destino."
                                    tamanho_final = os.path.getsize(caminho_final)
                                    sha_final = sha_cand
                                    break

                                os.replace(caminho_tmp, caminho_final)
                                tamanho_final = os.path.getsize(caminho_final)
                                sha_final = sha_cand
                                hashes_baixados[sha_final] = nome_destino
                                if doi:
                                    dois_baixados[doi] = nome_destino
                                status_final = "BAIXADO_COM_SUCESSO"
                                msg_final = ""
                                break

                except requests.exceptions.Timeout as exc:
                    status_final = "ERRO_TIMEOUT"
                    msg_final = f"Timeout ({timeout}s) ao baixar PDF: {exc}"
                except requests.exceptions.ConnectionError as exc:
                    status_final = "ERRO_CONEXAO"
                    msg_final = f"Erro de conexão ao baixar PDF: {exc}"
                except requests.exceptions.RequestException as exc:
                    status_final = "FALHA_APOS_TENTATIVAS"
                    msg_final = f"Falha de requisição HTTP: {exc}"

                if os.path.exists(caminho_tmp):
                    try:
                        os.remove(caminho_tmp)
                    except Exception:
                        pass

                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": url,
                    "tentativa": tentativa,
                    "data_hora": ts_tent,
                    "status_download": status_final,
                    "arquivo_pdf": "",
                    "tamanho_bytes": 0,
                    "sha256": "",
                    "mensagem_erro": msg_final,
                })

                if status_final in {"HTTP_404", "ACESSO_RESTRITO", "RESPOSTA_NAO_PDF", "ERRO_GRAVACAO", "DOWNLOAD_CANCELADO"}:
                    break
                if tentativa < max_tentativas:
                    time.sleep(min(4.0, 0.6 * (2 ** (tentativa - 1))))

            if status_final == "FALHA_APOS_TENTATIVAS" and tentativa_usada >= max_tentativas:
                msg_final = f"Falha após {max_tentativas} tentativa(s): {msg_final}"

            reg["status_download"] = status_final if status_final in STATUS_DOWNLOAD_VALIDOS else "FALHA_APOS_TENTATIVAS"
            reg["mensagem_download"] = msg_final
            reg["tentativas_download"] = tentativa_usada
            reg["data_tentativa_download"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if status_final == "BAIXADO_COM_SUCESSO":
                baixados_ok += 1
                reg["arquivo_pdf"] = nome_destino
                reg["tamanho_pdf_bytes"] = tamanho_final
                reg["sha256_pdf"] = sha_final
                if on_log:
                    on_log(f"[PDF OK] {nome_destino} ({tamanho_final} bytes)")
                if on_metrica_pdf:
                    on_metrica_pdf(baixados_ok + ja_existiam_ok)
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": url,
                    "tentativa": tentativa_usada,
                    "data_hora": reg["data_tentativa_download"],
                    "status_download": status_final,
                    "arquivo_pdf": nome_destino,
                    "tamanho_bytes": tamanho_final,
                    "sha256": sha_final,
                    "mensagem_erro": "",
                })
            elif status_final == "JA_EXISTIA_VALIDADO":
                ja_existiam_ok += 1
                reg["arquivo_pdf"] = nome_destino
                reg["tamanho_pdf_bytes"] = tamanho_final
                reg["sha256_pdf"] = sha_final
                if on_metrica_pdf:
                    on_metrica_pdf(baixados_ok + ja_existiam_ok)
                historico.append({
                    "id_interno": reg.get("id_interno", ""),
                    "plataforma": reg.get("plataforma", ""),
                    "string_busca": reg.get("string_busca", ""),
                    "titulo": reg.get("titulo", ""),
                    "doi": doi,
                    "url_pdf": url,
                    "tentativa": tentativa_usada,
                    "data_hora": reg["data_tentativa_download"],
                    "status_download": status_final,
                    "arquivo_pdf": nome_destino,
                    "tamanho_bytes": tamanho_final,
                    "sha256": sha_final,
                    "mensagem_erro": "",
                })
            else:
                falhas += 1
                reg["arquivo_pdf"] = ""
                reg["tamanho_pdf_bytes"] = 0
                reg["sha256_pdf"] = ""
                if on_log and status_final != "DOWNLOAD_CANCELADO":
                    on_log(f"[PDF {status_final}] {reg.get('titulo', '')[:45]} -> {msg_final}")
                if status_final != "DOWNLOAD_CANCELADO":
                    historico.append({
                        "id_interno": reg.get("id_interno", ""),
                        "plataforma": reg.get("plataforma", ""),
                        "string_busca": reg.get("string_busca", ""),
                        "titulo": reg.get("titulo", ""),
                        "doi": doi,
                        "url_pdf": url,
                        "tentativa": tentativa_usada,
                        "data_hora": reg["data_tentativa_download"],
                        "status_download": status_final,
                        "arquivo_pdf": "",
                        "tamanho_bytes": 0,
                        "sha256": "",
                        "mensagem_erro": msg_final,
                    })

            time.sleep(0.25)
    finally:
        session.close()

    return baixados_ok, ja_existiam_ok, falhas, historico