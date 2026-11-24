from __future__ import annotations

import csv
import glob
import json
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import requests

from conectores import requisicao_resiliente


def _to_int_safe(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(float(s.replace(",", ".")))
        except ValueError:
            return None
    return None


def _extrair_issns(issn_raw: Any) -> list[str]:
    if not issn_raw:
        return []
    encontrados: list[str] = []
    for parte in re.split(r"[;,|\s/]+", str(issn_raw)):
        m = re.search(r"\b(\d{4}-?\d{3}[\dXx])\b", parte)
        if m:
            val = m.group(1).upper()
            if "-" not in val:
                val = f"{val[:4]}-{val[4:]}"
            if val not in encontrados:
                encontrados.append(val)
    return encontrados


def _limpar_issn(issn_raw: Any) -> str:
    issns = _extrair_issns(issn_raw)
    return issns[0] if issns else ""


def _normalizar_nome_revista(nome: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(nome or "")).encode("ASCII", "ignore").decode("ASCII")
    txt = txt.lower().replace("&", " and ")
    txt = re.sub(r"\([^)]*\)", " ", txt)
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _quartil_por_percentil_h(h_index: int | None) -> str:
    if h_index is None:
        return "N/D"
    if h_index >= 120:
        return "Q1 (Est.)"
    if h_index >= 55:
        return "Q2 (Est.)"
    if h_index >= 20:
        return "Q3 (Est.)"
    return "Q4 (Est.)"


def _localizar_csv_scimago(pasta_saida: str) -> str:
    raiz_projeto = Path(__file__).resolve().parent
    home_dir = Path.home()
    pastas_busca = [
        raiz_projeto,
        Path(pasta_saida).resolve() if pasta_saida else raiz_projeto,
        home_dir,
        Path.cwd(),
    ]
    candidatos: list[Path] = []
    vistos: set[str] = set()
    for pasta in pastas_busca:
        if not pasta.exists():
            continue
        for arq in sorted(pasta.glob("scimagojr*.csv"), reverse=True):
            key = str(arq.resolve()).lower()
            if key not in vistos and arq.is_file():
                vistos.add(key)
                candidatos.append(arq)
    if candidatos:
        return str(candidatos[0])
    return ""


def _carregar_indice_scimago(csv_path: str, on_log: Callable[[str], None] | None = None) -> dict[str, Any]:
    indice: dict[str, Any] = {
        "ativo": False,
        "arquivo": csv_path,
        "por_issn": {},
        "por_nome": {},
        "lista_nomes": [],
    }
    if not csv_path or not os.path.exists(csv_path):
        return indice

    try:
        with open(csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            amostra = f.read(4096)
            f.seek(0)
            delimitador = ";" if amostra.count(";") >= amostra.count(",") else ","
            reader = csv.DictReader(f, delimiter=delimitador)
            if not reader.fieldnames:
                return indice

            mapa_cols = {c.strip().lower(): c for c in reader.fieldnames if c}
            col_title = mapa_cols.get("title", "")
            col_issn = mapa_cols.get("issn", "")
            col_sjr = mapa_cols.get("sjr", "")
            col_q = mapa_cols.get("sjr best quartile", "")
            col_h = mapa_cols.get("h index", "")

            if not col_title or not col_h:
                return indice

            por_issn: dict[str, dict[str, Any]] = {}
            por_nome: dict[str, dict[str, Any]] = {}
            lista_nomes: list[tuple[str, dict[str, Any]]] = []

            for row in reader:
                titulo_orig = str(row.get(col_title) or "").strip()
                if not titulo_orig:
                    continue

                h_int = _to_int_safe(row.get(col_h))
                sjr_raw = str(row.get(col_sjr) or "").strip().replace(",", ".") if col_sjr else ""
                quartil_raw = str(row.get(col_q) or "").strip().upper() if col_q else ""
                if not quartil_raw or quartil_raw == "-":
                    quartil_raw = "N/D"

                issns = _extrair_issns(row.get(col_issn)) if col_issn else []
                registro_scimago = {
                    "titulo_scimago": titulo_orig,
                    "issn": ", ".join(issns) if issns else "-",
                    "h_index": h_int if h_int is not None else "",
                    "sjr": sjr_raw if sjr_raw else "N/D",
                    "quartil": quartil_raw,
                }

                for cod_issn in issns:
                    if cod_issn not in por_issn:
                        por_issn[cod_issn] = registro_scimago

                nome_norm = _normalizar_nome_revista(titulo_orig)
                if nome_norm:
                    if nome_norm not in por_nome:
                        por_nome[nome_norm] = registro_scimago
                    lista_nomes.append((nome_norm, registro_scimago))

            indice["ativo"] = True
            indice["por_issn"] = por_issn
            indice["por_nome"] = por_nome
            indice["lista_nomes"] = lista_nomes
            if on_log:
                on_log(f"[SCIMAGO CSV] Base carregada: {os.path.basename(csv_path)} ({len(lista_nomes)} periódicos)")
    except Exception as exc:
        if on_log:
            on_log(f"[SCIMAGO CSV] Falha ao ler '{csv_path}': {exc}")

    return indice


def _buscar_no_scimago(
    indice_scimago: dict[str, Any],
    periodico: str,
    issn_raw: str,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    if not indice_scimago.get("ativo"):
        return None

    por_issn: dict[str, dict[str, Any]] = indice_scimago["por_issn"]
    por_nome: dict[str, dict[str, Any]] = indice_scimago["por_nome"]
    lista_nomes: list[tuple[str, dict[str, Any]]] = indice_scimago["lista_nomes"]

    # 1. Match exato por ISSN
    for cod_issn in _extrair_issns(issn_raw):
        if cod_issn in por_issn:
            hit = por_issn[cod_issn]
            h_val = hit["h_index"]
            return {
                "h_index": h_val,
                "h_index_exibicao": str(h_val) if h_val != "" else "N/D",
                "sjr": hit["sjr"],
                "quartil": hit["quartil"],
                "fonte_metrica": "SCImago_CSV (ISSN_Exato)",
                "notificacao": "",
            }

    nome_norm = _normalizar_nome_revista(periodico)
    if not nome_norm:
        return None

    # 2. Match exato por nome normalizado
    if nome_norm in por_nome:
        hit = por_nome[nome_norm]
        h_val = hit["h_index"]
        return {
            "h_index": h_val,
            "h_index_exibicao": str(h_val) if h_val != "" else "N/D",
            "sjr": hit["sjr"],
            "quartil": hit["quartil"],
            "fonte_metrica": "SCImago_CSV (Nome_Exato)",
            "notificacao": "",
        }

    # 3. Match aproximado conservador por similaridade de nome
    if len(nome_norm) >= 6:
        melhor_score = 0.0
        melhor_hit: dict[str, Any] | None = None
        len_alvo = len(nome_norm)

        for cand_norm, cand_data in lista_nomes:
            if abs(len(cand_norm) - len_alvo) > max(8, int(len_alvo * 0.35)):
                continue
            if cand_norm[:2] != nome_norm[:2]:
                continue
            ratio = SequenceMatcher(None, nome_norm, cand_norm).ratio()
            if ratio > melhor_score:
                melhor_score = ratio
                melhor_hit = cand_data

        if melhor_hit is not None and melhor_score >= 0.86:
            h_val = melhor_hit["h_index"]
            aviso = f"h-index aproximado: {h_val} (match {melhor_score:.0%} com '{melhor_hit['titulo_scimago']}')" if h_val != "" else ""
            if on_log and aviso:
                on_log(f"[SCIMAGO APROX] '{periodico}' -> {aviso}")
            return {
                "h_index": h_val,
                "h_index_exibicao": f"h-index aproximado: {h_val}" if h_val != "" else "N/D",
                "sjr": melhor_hit["sjr"],
                "quartil": melhor_hit["quartil"],
                "fonte_metrica": f"SCImago_CSV (Aproximado {melhor_score:.0%})",
                "notificacao": aviso,
            }

    return None


def _carregar_cache_metricas(cache_path: str) -> dict[str, dict[str, Any]]:
    if not cache_path or not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _salvar_cache_metricas(cache_path: str, cache: dict[str, dict[str, Any]]) -> None:
    if not cache_path:
        return
    tmp = f"{cache_path}.part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cache_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _consultar_openalex_source(
    session: requests.Session,
    periodico: str,
    issn: str,
    email: str,
    api_key: str,
    timeout: int,
    max_tentativas: int,
    controle: Any | None,
    on_evento: Callable[[dict[str, Any]], None] | None,
    on_log: Callable[[str], None] | None,
) -> dict[str, Any]:
    vazio: dict[str, Any] = {
        "h_index": "",
        "citescore": "",
        "encontrado": False,
    }

    issn_limpo = _limpar_issn(issn)
    nome_limpo = _normalizar_nome_revista(periodico)
    if not issn_limpo and not nome_limpo:
        return vazio

    params: dict[str, Any] = {"mailto": email, "per-page": 3}
    if api_key.strip():
        params["api_key"] = api_key.strip()

    if issn_limpo:
        params["filter"] = f"issn:{issn_limpo}"
    else:
        params["search"] = periodico.strip()

    try:
        resp = requisicao_resiliente(
            session=session,
            method="GET",
            url="https://api.openalex.org/sources",
            plataforma="OpenAlex_Sources",
            string_busca=issn_limpo or periodico,
            operacao="METRICAS_REVISTA",
            params=params,
            timeout=timeout,
            max_tentativas=max_tentativas,
            delay_base=0.20,
            controle=controle,
            on_evento=on_evento,
            on_log=on_log,
        )
        payload = resp.json()
        resultados = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(resultados, list) or not resultados:
            return vazio

        src_escolhido: dict[str, Any] | None = None
        if issn_limpo:
            src_escolhido = resultados[0] if isinstance(resultados[0], dict) else None
        else:
            for cand in resultados:
                if not isinstance(cand, dict):
                    continue
                cand_nome = _normalizar_nome_revista(cand.get("display_name"))
                if cand_nome == nome_limpo or SequenceMatcher(None, nome_limpo, cand_nome).ratio() >= 0.88:
                    src_escolhido = cand
                    break

        if not src_escolhido:
            return vazio

        stats = src_escolhido.get("summary_stats") if isinstance(src_escolhido.get("summary_stats"), dict) else {}
        h_int = _to_int_safe(stats.get("h_index"))
        citedness = stats.get("2yr_mean_citedness")
        citescore_val: float | str = ""
        if isinstance(citedness, (int, float)) and not isinstance(citedness, bool):
            citescore_val = round(float(citedness), 2)

        return {
            "h_index": h_int if h_int is not None else "",
            "citescore": citescore_val,
            "encontrado": True,
        }
    except Exception:
        return vazio


def enriquecer_metricas_revistas(
    registros: list[dict[str, Any]],
    pasta_saida: str,
    email: str,
    api_key: str = "",
    timeout: int = 30,
    max_tentativas: int = 3,
    controle: Any | None = None,
    on_evento: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    csv_scimago = _localizar_csv_scimago(pasta_saida)
    indice_scimago = _carregar_indice_scimago(csv_scimago, on_log=on_log)
    tag_scimago = os.path.basename(csv_scimago) if indice_scimago.get("ativo") else "SEM_CSV"

    cache_path = os.path.join(pasta_saida, "cache_metricas_revistas.json")
    cache = _carregar_cache_metricas(cache_path)
    resumo_revistas: dict[str, dict[str, Any]] = {}

    session = requests.Session()
    session.headers.update({
        "User-Agent": f"PesquisanatorXP/3.2 (mailto:{email})",
        "Accept": "application/json",
    })

    try:
        for reg in registros:
            if controle and controle.esta_cancelado():
                break

            periodico = str(reg.get("periodico") or "").strip()
            issn_raw = str(reg.get("issn") or "").strip()
            issn = _limpar_issn(issn_raw)
            chave_base = issn or _normalizar_nome_revista(periodico)

            if not chave_base:
                reg["h_index_revista"] = ""
                reg["h_index_exibicao"] = "N/D"
                reg["sjr_revista"] = "N/D"
                reg["citescore_revista"] = ""
                reg["quartil_revista"] = "N/D"
                reg["fonte_metrica_revista"] = "SEM_PERIODICO"
                reg["notificacao_metrica"] = ""
                continue

            chave_cache = f"{tag_scimago}::{chave_base}"
            if chave_cache not in cache:
                hit_scimago = _buscar_no_scimago(indice_scimago, periodico, issn_raw, on_log=on_log)
                oa_data = _consultar_openalex_source(
                    session=session,
                    periodico=periodico,
                    issn=issn,
                    email=email,
                    api_key=api_key,
                    timeout=timeout,
                    max_tentativas=max_tentativas,
                    controle=controle,
                    on_evento=on_evento,
                    on_log=on_log,
                )

                if hit_scimago is not None:
                    h_final = hit_scimago["h_index"]
                    h_exib = hit_scimago["h_index_exibicao"]
                    sjr_final = hit_scimago["sjr"]
                    quartil_final = hit_scimago["quartil"]
                    fonte_final = hit_scimago["fonte_metrica"]
                    notif_final = hit_scimago["notificacao"]
                elif oa_data.get("encontrado"):
                    h_final = oa_data.get("h_index", "")
                    h_exib = f"{h_final} (OpenAlex)" if h_final != "" else "N/D"
                    sjr_final = "N/D (Fora do SCImago CSV)"
                    h_int_oa = _to_int_safe(h_final)
                    quartil_final = _quartil_por_percentil_h(h_int_oa)
                    fonte_final = "OpenAlex_Sources (Fallback)"
                    notif_final = ""
                else:
                    h_final = ""
                    h_exib = "N/D"
                    sjr_final = "N/D"
                    quartil_final = "N/D"
                    fonte_final = "NAO_ENCONTRADO"
                    notif_final = ""

                cache[chave_cache] = {
                    "h_index": h_final,
                    "h_index_exibicao": h_exib,
                    "sjr": sjr_final,
                    "citescore": oa_data.get("citescore", ""),
                    "quartil": quartil_final,
                    "fonte_metrica": fonte_final,
                    "notificacao": notif_final,
                    "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                _salvar_cache_metricas(cache_path, cache)

            m = cache[chave_cache]
            h_val = m.get("h_index", "")
            h_exib_val = str(m.get("h_index_exibicao") or (h_val if h_val != "" else "N/D"))
            reg["h_index_revista"] = h_val if h_val is not None else ""
            reg["h_index_exibicao"] = h_exib_val
            reg["sjr_revista"] = str(m.get("sjr") or "N/D")
            cs_val = m.get("citescore", "")
            reg["citescore_revista"] = cs_val if cs_val is not None else ""
            reg["quartil_revista"] = str(m.get("quartil") or "N/D")
            reg["fonte_metrica_revista"] = str(m.get("fonte_metrica") or "")
            reg["notificacao_metrica"] = str(m.get("notificacao") or "")

            if chave_base not in resumo_revistas:
                resumo_revistas[chave_base] = {
                    "periodico": periodico or "Nao_Informado",
                    "issn": issn or "-",
                    "h_index": reg["h_index_revista"],
                    "h_index_exibicao": reg["h_index_exibicao"],
                    "sjr": reg["sjr_revista"],
                    "citescore": reg["citescore_revista"],
                    "quartil": reg["quartil_revista"],
                    "fonte_metrica": reg["fonte_metrica_revista"],
                    "notificacao": reg["notificacao_metrica"],
                    "qtd_artigos": 0,
                }
            resumo_revistas[chave_base]["qtd_artigos"] = int(resumo_revistas[chave_base].get("qtd_artigos") or 0) + 1
    finally:
        session.close()

    return resumo_revistas
