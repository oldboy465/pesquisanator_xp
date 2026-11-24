from __future__ import annotations

import random
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse

import requests

DEFAULT_DELAYS: dict[str, float] = {
    "OpenAlex": 0.25,
    "CrossRef": 0.50,
    "EuropePMC": 0.30,
    "DOAJ": 0.35,
    "arXiv": 3.00,
}

STATUS_RETENTAVEIS = {408, 425, 429, 500, 502, 503, 504}


def criar_sessao_http(email: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": f"PesquisanatorXP/3.0 (mailto:{email})",
        "Accept": "application/json, application/xml, text/xml, */*",
    })
    return session


def validar_url_segura(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def limpar_doi(doi_raw: str) -> str:
    if not doi_raw:
        return ""
    limpo = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi_raw.strip(), flags=re.I)
    return limpo.strip().lower()


def reconstruir_resumo_openalex(inv_idx: Any) -> str:
    if not inv_idx or not isinstance(inv_idx, dict):
        return ""
    mapa: dict[int, str] = {}
    for palavra, posicoes in inv_idx.items():
        if isinstance(posicoes, list):
            for p in posicoes:
                if isinstance(p, int):
                    mapa[p] = palavra
    if not mapa:
        return ""
    return " ".join(mapa[i] for i in sorted(mapa))


def normalizar_booleanos(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return ""
    q = re.sub(r"\b(and)\b", "AND", q, flags=re.I)
    q = re.sub(r"\b(or)\b", "OR", q, flags=re.I)
    q = re.sub(r"\b(not)\b", "NOT", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


def _parse_retry_after(header_val: str | None) -> float | None:
    if not header_val:
        return None
    val = header_val.strip()
    try:
        segundos = float(val)
        return max(0.5, min(segundos, 60.0))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.5, min(delta, 60.0))
    except Exception:
        return None


def requisicao_resiliente(
    session: requests.Session,
    method: str,
    url: str,
    plataforma: str,
    string_busca: str,
    operacao: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 0.5,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> requests.Response:
    if not validar_url_segura(url):
        raise ValueError(f"URL inválida ou insegura: {url}")

    ultimo_erro: Exception | None = None
    for tentativa in range(1, max(1, max_tentativas) + 1):
        if controle and controle.esta_cancelado():
            raise RuntimeError("Execução cancelada pelo usuário.")
        if controle:
            controle.aguardar_se_pausado()
            if controle.esta_cancelado():
                raise RuntimeError("Execução cancelada pelo usuário.")

        t0 = time.monotonic()
        try:
            resp = session.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            codigo = resp.status_code

            if codigo == 429 or codigo in STATUS_RETENTAVEIS:
                retry_hdr = _parse_retry_after(resp.headers.get("Retry-After"))
                jitter = random.uniform(0.15, 0.85)
                espera = retry_hdr if retry_hdr is not None else (delay_base * (2 ** (tentativa - 1))) + jitter
                resultado = "RATE_LIMIT_429" if codigo == 429 else "TENTATIVA_FALHA"
                if on_evento:
                    on_evento({
                        "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "plataforma": plataforma,
                        "string_busca": string_busca,
                        "operacao": operacao,
                        "codigo_http": codigo,
                        "tentativa": tentativa,
                        "tempo_espera": round(espera, 2),
                        "resultado": resultado,
                        "descricao_erro": f"HTTP {codigo} recebido em {url}",
                    })
                if tentativa < max_tentativas:
                    if on_log:
                        on_log(f"[{plataforma}] HTTP {codigo} (tentativa {tentativa}/{max_tentativas}). Aguardando {espera:.1f}s...")
                    time.sleep(espera)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            if on_evento:
                on_evento({
                    "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "plataforma": plataforma,
                    "string_busca": string_busca,
                    "operacao": operacao,
                    "codigo_http": codigo,
                    "tentativa": tentativa,
                    "tempo_espera": round(time.monotonic() - t0, 2),
                    "resultado": "SUCESSO",
                    "descricao_erro": "",
                })
            return resp

        except requests.exceptions.Timeout as exc:
            ultimo_erro = exc
            espera = (delay_base * (2 ** (tentativa - 1))) + random.uniform(0.1, 0.6)
            if on_evento:
                on_evento({
                    "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "plataforma": plataforma,
                    "string_busca": string_busca,
                    "operacao": operacao,
                    "codigo_http": "TIMEOUT",
                    "tentativa": tentativa,
                    "tempo_espera": round(espera, 2),
                    "resultado": "FALHA_APOS_TENTATIVAS" if tentativa == max_tentativas else "TENTATIVA_FALHA",
                    "descricao_erro": f"Timeout ({timeout}s): {exc}",
                })
            if tentativa < max_tentativas:
                time.sleep(espera)
        except requests.exceptions.RequestException as exc:
            ultimo_erro = exc
            resp_obj = getattr(exc, "response", None)
            codigo_http: int | str = resp_obj.status_code if resp_obj is not None else "ERRO_REDE"
            espera = (delay_base * (2 ** (tentativa - 1))) + random.uniform(0.1, 0.6)
            if on_evento:
                on_evento({
                    "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "plataforma": plataforma,
                    "string_busca": string_busca,
                    "operacao": operacao,
                    "codigo_http": codigo_http,
                    "tentativa": tentativa,
                    "tempo_espera": round(espera, 2),
                    "resultado": "FALHA_APOS_TENTATIVAS" if tentativa == max_tentativas else "TENTATIVA_FALHA",
                    "descricao_erro": f"{type(exc).__name__}: {exc}",
                })
            if tentativa < max_tentativas:
                time.sleep(espera)

    if ultimo_erro:
        raise ultimo_erro
    raise RuntimeError(f"Falha na requisição para {plataforma}")


def _montar_registro_padrao(
    plataforma: str,
    string_busca: str,
    titulo: str = "",
    autores: str = "",
    ano: str = "",
    idioma: str = "",
    tipo: str = "",
    periodico: str = "",
    editora: str = "",
    doi: str = "",
    identificador_externo: str = "",
    issn: str = "",
    resumo: str = "",
    landing_url: str = "",
    pdf_url: str = "",
    acesso_aberto: str = "Nao_Informado",
    data_publicacao: str = "",
) -> dict[str, Any]:
    return {
        "plataforma": plataforma,
        "string_busca": string_busca,
        "titulo": (titulo or "").strip(),
        "autores": (autores or "").strip(),
        "periodico": (periodico or "").strip(),
        "editora": (editora or "").strip(),
        "ano": (ano or "").strip(),
        "idioma": (idioma or "").strip().lower(),
        "tipo": (tipo or "").strip(),
        "doi": limpar_doi(doi),
        "identificador_externo": (identificador_externo or "").strip(),
        "issn": (issn or "").strip(),
        "acesso_aberto": acesso_aberto or "Nao_Informado",
        "landing_url": (landing_url or "").strip(),
        "pdf_url": (pdf_url or "").strip(),
        "resumo": (resumo or "").strip(),
        "data_publicacao": (data_publicacao or "").strip(),
        "data_recuperacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "h_index_revista": "",
        "sjr_revista": "N/D",
        "citescore_revista": "",
        "quartil_revista": "N/D",
        "fonte_metrica_revista": "",
    }


def buscar_openalex(
    session: requests.Session,
    query: str,
    ano_ini: int,
    ano_fim: int,
    idiomas: list[str],
    apenas_periodicos: bool,
    apenas_oa: bool,
    email: str,
    api_key: str,
    limite: int,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 0.25,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    filtros = [f"publication_year:{ano_ini}-{ano_fim}"]
    if apenas_periodicos:
        filtros.append("type:article")
    if apenas_oa:
        filtros.append("is_oa:true")
    if idiomas:
        filtros.append("language:" + "|".join(idiomas))

    q_norm = normalizar_booleanos(query)
    cursor = "*"
    resultados: list[dict[str, Any]] = []
    pagina = 0

    while len(resultados) < limite and cursor:
        if controle and controle.esta_cancelado():
            break
        pagina += 1
        por_pagina = min(100, limite - len(resultados))
        params: dict[str, Any] = {
            "search": q_norm,
            "filter": ",".join(filtros),
            "per-page": por_pagina,
            "cursor": cursor,
            "mailto": email,
        }
        if api_key.strip():
            params["api_key"] = api_key.strip()

        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url="https://api.openalex.org/works",
            plataforma="OpenAlex",
            string_busca=query,
            operacao=f"PAGINA_{pagina}",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=delay_base,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        payload = resp.json()
        itens = payload.get("results") or []
        if not itens:
            break

        for w in itens:
            best_oa = w.get("best_oa_location") or {}
            prim = w.get("primary_location") or {}
            oa = w.get("open_access") or {}
            source = prim.get("source") or {}
            pdf_url = best_oa.get("pdf_url") or prim.get("pdf_url") or oa.get("oa_url") or ""
            landing = best_oa.get("landing_page_url") or prim.get("landing_page_url") or w.get("id") or ""
            autores = "; ".join(
                a.get("author", {}).get("display_name", "")
                for a in (w.get("authorships") or [])[:8]
                if a.get("author", {}).get("display_name")
            )
            issns = source.get("issn") or []
            issn_str = ", ".join(issns) if isinstance(issns, list) else str(issns or "")
            resultados.append(_montar_registro_padrao(
                plataforma="OpenAlex",
                string_busca=query,
                titulo=w.get("title") or w.get("display_name") or "",
                autores=autores,
                ano=str(w.get("publication_year") or ""),
                idioma=(w.get("language") or "").lower(),
                tipo=w.get("type") or "",
                periodico=source.get("display_name") or "",
                editora=source.get("host_organization_name") or "",
                doi=w.get("doi") or "",
                identificador_externo=w.get("id") or "",
                issn=issn_str,
                resumo=reconstruir_resumo_openalex(w.get("abstract_inverted_index")),
                landing_url=landing,
                pdf_url=pdf_url,
                acesso_aberto="Sim" if oa.get("is_oa") else "Nao",
                data_publicacao=str(w.get("publication_date") or ""),
            ))
            if len(resultados) >= limite:
                break

        cursor = (payload.get("meta") or {}).get("next_cursor")
        time.sleep(delay_base)

    return resultados


def buscar_crossref(
    session: requests.Session,
    query: str,
    ano_ini: int,
    ano_fim: int,
    idiomas: list[str],
    apenas_periodicos: bool,
    email: str,
    limite: int,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 0.50,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    filtros = [
        f"from-pub-date:{ano_ini}-01-01",
        f"until-pub-date:{ano_fim}-12-31",
    ]
    if apenas_periodicos:
        filtros.append("type:journal-article")

    q_norm = normalizar_booleanos(query)
    resultados: list[dict[str, Any]] = []
    offset = 0
    pagina = 0

    while len(resultados) < limite:
        if controle and controle.esta_cancelado():
            break
        pagina += 1
        rows = min(100, limite - len(resultados))
        params = {
            "query.bibliographic": q_norm,
            "filter": ",".join(filtros),
            "rows": rows,
            "offset": offset,
            "mailto": email,
        }
        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url="https://api.crossref.org/works",
            plataforma="CrossRef",
            string_busca=query,
            operacao=f"PAGINA_{pagina}",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=delay_base,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        itens = (resp.json().get("message") or {}).get("items") or []
        if not itens:
            break

        for it in itens:
            lang = (it.get("language") or "").lower()
            if idiomas and lang and lang not in idiomas:
                continue

            pdf_url = ""
            for lk in (it.get("link") or []):
                u = lk.get("URL") or ""
                ct = (lk.get("content-type") or "").lower()
                if "pdf" in ct or u.lower().endswith(".pdf"):
                    pdf_url = u
                    break

            dp = ((it.get("issued") or {}).get("date-parts") or [[""]])[0]
            ano = str(dp[0]) if dp else ""
            data_pub = "-".join(str(x) for x in dp if x) if dp else ""
            autores = "; ".join(
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (it.get("author") or [])[:8]
            )
            titulos = it.get("title") or []
            periodicos = it.get("container-title") or []
            issns = it.get("ISSN") or []
            resultados.append(_montar_registro_padrao(
                plataforma="CrossRef",
                string_busca=query,
                titulo=titulos[0] if titulos else "",
                autores=autores,
                ano=ano,
                idioma=lang,
                tipo=it.get("type") or "",
                periodico=periodicos[0] if periodicos else "",
                editora=it.get("publisher") or "",
                doi=it.get("DOI") or "",
                identificador_externo=it.get("DOI") or "",
                issn=", ".join(str(x) for x in issns),
                resumo=re.sub(r"<[^>]+>", "", it.get("abstract") or "").strip(),
                landing_url=((it.get("resource") or {}).get("primary") or {}).get("URL") or (f"https://doi.org/{it.get('DOI')}" if it.get("DOI") else ""),
                pdf_url=pdf_url,
                acesso_aberto="Sim" if pdf_url else "Verificar",
                data_publicacao=data_pub,
            ))
            if len(resultados) >= limite:
                break

        offset += len(itens)
        if len(itens) < rows:
            break
        time.sleep(delay_base)

    return resultados


def buscar_europepmc(
    session: requests.Session,
    query: str,
    ano_ini: int,
    ano_fim: int,
    idiomas: list[str],
    apenas_oa: bool,
    email: str,
    limite: int,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 0.30,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    q = f"({normalizar_booleanos(query)}) AND PUB_YEAR:[{ano_ini} TO {ano_fim}]"
    if apenas_oa:
        q += " AND OPEN_ACCESS:y"
    if idiomas:
        q += " AND (" + " OR ".join(f'LANG:"{l}"' for l in idiomas) + ")"

    cursor_mark = "*"
    resultados: list[dict[str, Any]] = []
    pagina = 0

    while len(resultados) < limite and cursor_mark:
        if controle and controle.esta_cancelado():
            break
        pagina += 1
        page_size = min(100, limite - len(resultados))
        params = {
            "query": q,
            "resultType": "core",
            "pageSize": page_size,
            "cursorMark": cursor_mark,
            "format": "json",
            "email": email,
        }
        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            plataforma="EuropePMC",
            string_busca=query,
            operacao=f"PAGINA_{pagina}",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=delay_base,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        data = resp.json()
        itens = (data.get("resultList") or {}).get("result") or []
        if not itens:
            break

        for it in itens:
            pmid = it.get("pmid") or ""
            pmcid = it.get("pmcid") or ""
            ext_id = f"PMCID:{pmcid}" if pmcid else (f"PMID:{pmid}" if pmid else (it.get("id") or ""))
            pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
            landing = f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://europepmc.org/articles/{pmcid}" if pmcid else "")
            resultados.append(_montar_registro_padrao(
                plataforma="EuropePMC",
                string_busca=query,
                titulo=it.get("title") or "",
                autores=it.get("authorString") or "",
                ano=str(it.get("pubYear") or ""),
                idioma=(it.get("language") or "").lower(),
                tipo=it.get("pubType") or "article",
                periodico=it.get("journalTitle") or "",
                editora=it.get("publisher") or "",
                doi=it.get("doi") or "",
                identificador_externo=ext_id,
                issn=it.get("issn") or "",
                resumo=re.sub(r"<[^>]+>", "", it.get("abstractText") or "").strip(),
                landing_url=landing,
                pdf_url=pdf_url,
                acesso_aberto="Sim" if it.get("isOpenAccess") == "Y" else "Verificar",
                data_publicacao=str(it.get("firstPublicationDate") or it.get("pubYear") or ""),
            ))
            if len(resultados) >= limite:
                break

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor_mark or len(itens) < page_size:
            break
        cursor_mark = next_cursor
        time.sleep(delay_base)

    return resultados


def buscar_doaj(
    session: requests.Session,
    query: str,
    ano_ini: int,
    ano_fim: int,
    email: str,
    limite: int,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 0.35,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(normalizar_booleanos(query), safe="")
    url = f"https://doaj.org/api/search/articles/{encoded}"
    resultados: list[dict[str, Any]] = []
    pagina = 0

    while len(resultados) < limite:
        if controle and controle.esta_cancelado():
            break
        pagina += 1
        page_size = min(100, limite - len(resultados))
        params = {"page": pagina, "pageSize": page_size}
        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url=url,
            plataforma="DOAJ",
            string_busca=query,
            operacao=f"PAGINA_{pagina}",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=delay_base,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        itens = resp.json().get("results") or []
        if not itens:
            break

        for it in itens:
            bib = it.get("bibjson") or {}
            ano_str = str(bib.get("year") or "")
            anos_match = re.findall(r"\d{4}", ano_str)
            ano_int = int(anos_match[0]) if anos_match else None
            if ano_int is not None and not (ano_ini <= ano_int <= ano_fim):
                continue

            pdf_url = ""
            landing = ""
            for lk in (bib.get("link") or []):
                u = lk.get("url") or ""
                tp = (lk.get("type") or "").lower()
                if tp == "fulltext" and not landing:
                    landing = u
                if "pdf" in u.lower() or tp == "pdf":
                    pdf_url = u

            doi = ""
            for ident in (bib.get("identifier") or []):
                if (ident.get("type") or "").lower() == "doi":
                    doi = ident.get("id") or ""

            autores = "; ".join(
                a.get("name", "") for a in (bib.get("author") or [])[:8] if a.get("name")
            )
            journal = bib.get("journal") or {}
            resultados.append(_montar_registro_padrao(
                plataforma="DOAJ",
                string_busca=query,
                titulo=bib.get("title") or "",
                autores=autores,
                ano=ano_str,
                idioma="",
                tipo="journal-article",
                periodico=journal.get("title") or "",
                editora=journal.get("publisher") or "",
                doi=doi,
                identificador_externo=it.get("id") or "",
                issn=", ".join(journal.get("issns") or []) if isinstance(journal.get("issns"), list) else "",
                resumo=bib.get("abstract") or "",
                landing_url=landing,
                pdf_url=pdf_url,
                acesso_aberto="Sim",
                data_publicacao=ano_str,
            ))
            if len(resultados) >= limite:
                break

        if len(itens) < page_size:
            break
        time.sleep(delay_base)

    return resultados


def buscar_arxiv(
    session: requests.Session,
    query: str,
    ano_ini: int,
    ano_fim: int,
    email: str,
    limite: int,
    timeout: int = 30,
    max_tentativas: int = 4,
    delay_base: float = 3.00,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    q_clean = normalizar_booleanos(query)
    resultados: list[dict[str, Any]] = []
    start = 0
    pagina = 0
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    while len(resultados) < limite:
        if controle and controle.esta_cancelado():
            break
        pagina += 1
        max_res = min(100, limite - len(resultados))
        params = {
            "search_query": f"all:{q_clean}",
            "start": start,
            "max_results": max_res,
        }
        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url="https://export.arxiv.org/api/query",
            plataforma="arXiv",
            string_busca=query,
            operacao=f"PAGINA_{pagina}",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=delay_base,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", ns)
        if not entries:
            break

        for entry in entries:
            pub_full = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
            pub = pub_full[:4]
            try:
                ano_int = int(pub)
            except ValueError:
                ano_int = None
            if ano_int is not None and not (ano_ini <= ano_int <= ano_fim):
                continue

            titulo = re.sub(r"\s+", " ", entry.findtext("atom:title", default="", namespaces=ns)).strip()
            resumo = re.sub(r"\s+", " ", entry.findtext("atom:summary", default="", namespaces=ns)).strip()
            landing = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            pdf_url = ""
            for lk in entry.findall("atom:link", ns):
                if lk.attrib.get("title") == "pdf" or lk.attrib.get("type") == "application/pdf":
                    pdf_url = lk.attrib.get("href", "")
            if not pdf_url and "/abs/" in landing:
                pdf_url = landing.replace("/abs/", "/pdf/") + ".pdf"

            autores = "; ".join(
                (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
                for a in entry.findall("atom:author", ns)[:8]
            )
            resultados.append(_montar_registro_padrao(
                plataforma="arXiv",
                string_busca=query,
                titulo=titulo,
                autores=autores,
                ano=pub,
                idioma="en",
                tipo="preprint",
                periodico="arXiv Repository",
                editora="Cornell University / arXiv",
                doi="",
                identificador_externo=landing,
                issn="",
                resumo=resumo,
                landing_url=landing,
                pdf_url=pdf_url,
                acesso_aberto="Sim",
                data_publicacao=pub_full[:10],
            ))
            if len(resultados) >= limite:
                break

        start += len(entries)
        if len(entries) < max_res:
            break
        time.sleep(delay_base)

    return resultados