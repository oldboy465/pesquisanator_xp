from __future__ import annotations

import json
import os
import re
from datetime import datetime
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
            return int(float(s))
        except ValueError:
            return None
    return None


def _limpar_issn(issn_raw: Any) -> str:
    if not issn_raw:
        return ""
    for parte in re.split(r"[;,|\s]+", str(issn_raw)):
        m = re.search(r"\b(\d{4}-?\d{3}[\dXx])\b", parte)
        if m:
            val = m.group(1).upper()
            if "-" not in val:
                val = f"{val[:4]}-{val[4:]}"
            return val
    return ""


def _normalizar_nome_revista(nome: Any) -> str:
    return re.sub(r"\s+", " ", str(nome or "").strip().lower())


def _quartil_por_percentil_h(h_index: int | None) -> str:
    if h_index is None:
        return "N/D"
    if h_index >= 120:
        return "Q1"
    if h_index >= 55:
        return "Q2"
    if h_index >= 20:
        return "Q3"
    return "Q4"


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
        "sjr": "N/D",
        "citescore": "",
        "quartil": "N/D",
        "fonte_metrica": "NAO_ENCONTRADO",
        "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    issn_limpo = _limpar_issn(issn)
    nome_limpo = _normalizar_nome_revista(periodico)
    if not issn_limpo and not nome_limpo:
        return vazio

    params: dict[str, Any] = {"mailto": email, "per-page": 1}
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

        src = resultados[0] if isinstance(resultados[0], dict) else {}
        stats = src.get("summary_stats") if isinstance(src.get("summary_stats"), dict) else {}
        h_int = _to_int_safe(stats.get("h_index"))
        citedness = stats.get("2yr_mean_citedness")
        citescore_val: float | str = ""
        if isinstance(citedness, (int, float)) and not isinstance(citedness, bool):
            citescore_val = round(float(citedness), 2)

        return {
            "h_index": h_int if h_int is not None else "",
            "sjr": "N/D (Scimago sem API)",
            "citescore": citescore_val,
            "quartil": _quartil_por_percentil_h(h_int),
            "fonte_metrica": "OpenAlex_Sources",
            "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    cache_path = os.path.join(pasta_saida, "cache_metricas_revistas.json")
    cache = _carregar_cache_metricas(cache_path)
    resumo_revistas: dict[str, dict[str, Any]] = {}

    session = requests.Session()
    session.headers.update({
        "User-Agent": f"PesquisanatorXP/3.1 (mailto:{email})",
        "Accept": "application/json",
    })

    try:
        for reg in registros:
            if controle and controle.esta_cancelado():
                break

            periodico = str(reg.get("periodico") or "").strip()
            issn = _limpar_issn(reg.get("issn"))
            chave = issn or _normalizar_nome_revista(periodico)

            if not chave:
                reg["h_index_revista"] = ""
                reg["sjr_revista"] = "N/D"
                reg["citescore_revista"] = ""
                reg["quartil_revista"] = "N/D"
                reg["fonte_metrica_revista"] = "SEM_PERIODICO"
                continue

            if chave not in cache:
                if on_log:
                    on_log(f"[MÉTRICAS] Consultando H-Index/CiteScore: {periodico or issn}")
                cache[chave] = _consultar_openalex_source(
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
                _salvar_cache_metricas(cache_path, cache)

            m = cache[chave]
            h_val = m.get("h_index", "")
            reg["h_index_revista"] = h_val if h_val is not None else ""
            reg["sjr_revista"] = str(m.get("sjr") or "N/D")
            cs_val = m.get("citescore", "")
            reg["citescore_revista"] = cs_val if cs_val is not None else ""
            reg["quartil_revista"] = str(m.get("quartil") or "N/D")
            reg["fonte_metrica_revista"] = str(m.get("fonte_metrica") or "")

            if chave not in resumo_revistas:
                resumo_revistas[chave] = {
                    "periodico": periodico or "Nao_Informado",
                    "issn": issn or "-",
                    "h_index": reg["h_index_revista"],
                    "sjr": reg["sjr_revista"],
                    "citescore": reg["citescore_revista"],
                    "quartil": reg["quartil_revista"],
                    "fonte_metrica": reg["fonte_metrica_revista"],
                    "qtd_artigos": 0,
                }
            resumo_revistas[chave]["qtd_artigos"] = int(resumo_revistas[chave].get("qtd_artigos") or 0) + 1
    finally:
        session.close()

    return resumo_revistas
