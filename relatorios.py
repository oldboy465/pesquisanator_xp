from __future__ import annotations

import csv
import os
import re
from datetime import datetime
from typing import Any, Callable

from processamento import limpar_nome_arquivo_windows

try:
    import openpyxl
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

NOMES_IDIOMAS: dict[str, str] = {
    "pt": "Português",
    "en": "Inglês",
    "es": "Espanhol",
    "fr": "Francês",
    "de": "Alemão",
    "it": "Italiano",
    "ru": "Russo",
    "zh": "Chinês",
    "ja": "Japonês",
    "ko": "Coreano",
    "ar": "Árabe",
    "la": "Latim",
}


def _to_int(val: Any, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return default
        try:
            return int(float(s))
        except ValueError:
            return default
    return default


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip().replace(",", ".")
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            return default
    return default


def _h_index_texto(registro: dict[str, Any]) -> str:
    exib = str(registro.get("h_index_exibicao") or "").strip()
    if exib and exib != "N/D":
        return exib
    raw = registro.get("h_index_revista")
    if raw in ("", None):
        raw = registro.get("h_index")
    return str(raw) if raw not in ("", None) else "N/D"


def _h_index_celula(registro: dict[str, Any]) -> int | str:
    exib = str(registro.get("h_index_exibicao") or "").strip()
    if "aproximado" in exib.lower():
        return exib
    raw = registro.get("h_index_revista")
    if raw in ("", None):
        raw = registro.get("h_index")
    if raw in ("", None):
        return ""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return str(raw)


def _escapar_bibtex(texto: Any) -> str:
    s = str(texto or "")
    s = s.replace("\\", "\\textbackslash{}")
    s = s.replace("{", "\\{").replace("}", "\\}")
    s = s.replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def exportar_csv(registros: list[dict[str, Any]], pasta_saida: str, on_log: Callable[[str], None] | None = None) -> str:
    csv_path = os.path.join(pasta_saida, "resultados_coleta.csv")
    colunas = [
        "id_interno",
        "plataforma_inicial",
        "plataformas_consolidadas",
        "string_busca_inicial",
        "strings_consolidadas",
        "titulo",
        "autores",
        "periodico",
        "h_index_revista",
        "h_index_exibicao",
        "quartil_revista",
        "citescore_revista",
        "sjr_revista",
        "fonte_metrica_revista",
        "notificacao_metrica",
        "editora",
        "ano",
        "idioma",
        "tipo",
        "doi",
        "identificador_externo",
        "issn",
        "acesso_aberto",
        "landing_url",
        "pdf_url",
        "situacao_triagem",
        "motivo_triagem",
        "status_dedup",
        "id_registro_original",
        "status_download",
        "arquivo_pdf",
        "tamanho_pdf_bytes",
        "sha256_pdf",
        "data_recuperacao",
        "resumo",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=colunas, extrasaction="ignore")
        writer.writeheader()
        for r in registros:
            writer.writerow(r)
    if on_log:
        on_log(f"[CSV] Exportado para: {csv_path}")
    return csv_path


def exportar_bibtex(registros: list[dict[str, Any]], pasta_saida: str, on_log: Callable[[str], None] | None = None) -> str:
    bib_path = os.path.join(pasta_saida, "referencias_coleta.bib")
    chaves_vistas: set[str] = set()
    with open(bib_path, "w", encoding="utf-8") as f:
        for i, r in enumerate(registros, start=1):
            autor_base = limpar_nome_arquivo_windows(str(r.get("autores") or "autor").split(";")[0], 18) or "autor"
            ano_match = re.findall(r"\d{4}", str(r.get("ano") or ""))
            ano_str = ano_match[0] if ano_match else "nd"
            chave = f"ref{i:03d}_{autor_base}_{ano_str}"
            if chave in chaves_vistas:
                chave = f"{chave}_{i}"
            chaves_vistas.add(chave)

            tipo_entry = "misc" if str(r.get("tipo") or "").lower() == "preprint" else "article"
            f.write(f"@{tipo_entry}{{{chave},\n")
            f.write(f"  title = {{{_escapar_bibtex(r.get('titulo'))}}},\n")
            f.write(f"  author = {{{_escapar_bibtex(r.get('autores'))}}},\n")
            if r.get("periodico"):
                f.write(f"  journal = {{{_escapar_bibtex(r.get('periodico'))}}},\n")
            if ano_match:
                f.write(f"  year = {{{ano_str}}},\n")
            if r.get("doi"):
                f.write(f"  doi = {{{_escapar_bibtex(r.get('doi'))}}},\n")
            url_ref = r.get("pdf_url") or r.get("landing_url")
            if url_ref:
                f.write(f"  url = {{{_escapar_bibtex(url_ref)}}},\n")
            f.write("}\n\n")
    if on_log:
        on_log(f"[BibTeX] Exportado para: {bib_path}")
    return bib_path


def gerar_relatorio_txt(
    pasta_saida: str,
    cfg: Any,
    stats: dict[str, Any],
    todos_registros: list[dict[str, Any]],
    aprovados: list[dict[str, Any]],
    excluidos: list[dict[str, Any]],
    ignorados: list[dict[str, Any]],
    stats_por_consulta: dict[tuple[str, str], dict[str, int]],
    eventos_execucao: list[dict[str, Any]],
    autores: str,
    contato: str,
    resumo_revistas: dict[str, dict[str, Any]] | None = None,
) -> str:
    txt_path = os.path.join(pasta_saida, "relatorio_coleta.txt")
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    revistas_map = resumo_revistas or {}

    linhas: list[str] = [
        "=" * 82,
        "RELATORIO DE COLETA BIBLIOGRAFICA E REVISAO SISTEMATICA - PESQUISANATOR XP",
        "=" * 82,
        f"Data/Hora da Execucao : {agora}",
        f"Autoria               : {autores} ({contato})",
        f"Periodo Consultado    : {cfg.ano_ini} a {cfg.ano_fim}",
        f"Idiomas               : {', '.join(cfg.idiomas) if cfg.idiomas else 'Todos'}",
        f"Plataformas           : {', '.join(cfg.plataformas)}",
        f"Limite por String     : {cfg.limite_por_string} | Limite Total: {cfg.limite_total or 'Sem teto extra'}",
        f"Timeout / Tentativas  : {cfg.timeout}s / {cfg.max_tentativas} tentativa(s)",
        f"Apenas Periodicos     : {'Sim' if cfg.apenas_periodicos else 'Nao'} | Open Access: {'Sim' if cfg.apenas_oa else 'Nao'}",
        f"Termos Inclusao       : {', '.join(cfg.termos_incl) if cfg.termos_incl else 'Nenhum'}",
        f"Termos Exclusao       : {', '.join(cfg.termos_excl) if cfg.termos_excl else 'Nenhum'}",
        "-" * 82,
        "1. FUNIL DE SELECAO E QUANTITATIVOS CONSOLIDADOS",
        "-" * 82,
        f"  Total bruto recuperado                : {stats['total_brutos']}",
        f"  Total de artigos unicos               : {stats['total_unicos']}",
        f"  Total de registros duplicados         : {stats['total_duplicados']}",
        f"  Total de artigos aprovados            : {stats['total_aprovados']}",
        f"  Total de artigos excluidos            : {stats['total_excluidos']}",
        f"  Total de registros ignorados          : {stats['total_ignorados']}",
        f"  PDFs com link disponivel              : {stats['pdfs_disponiveis']}",
        f"  PDFs baixados com sucesso             : {stats['pdfs_baixados']}",
        f"  PDFs ja existentes e validados        : {stats['pdfs_ja_existiam']}",
        f"  Falhas de download de PDF             : {stats['pdfs_falha']}",
        f"  Eventos/erros registrados             : {len(eventos_execucao)}",
        "-" * 82,
        "2. DESEMPENHO POR STRING E PLATAFORMA",
        "-" * 82,
    ]

    for (q, plat), st in stats_por_consulta.items():
        linhas.append(
            f"  [{plat:<10}] '{q}' -> Brutos: {st['brutos']} | Unicos: {st['unicos']} | "
            f"Aprovados: {st['aprovados']} | Excluidos: {st['excluidos']} | PDFs OK: {st['pdfs_baixados']}"
        )

    linhas.extend([
        "-" * 82,
        "3. ARTIGOS APROVADOS (COM REVISTA E METRICAS DE IMPACTO)",
        "-" * 82,
    ])
    for i, r in enumerate(aprovados, start=1):
        h_str = _h_index_texto(r)
        q_val = str(r.get("quartil_revista") or "N/D")
        cs_raw = r.get("citescore_revista")
        cs_str = str(cs_raw) if cs_raw not in ("", None) else "N/D"
        sjr_val = str(r.get("sjr_revista") or "N/D")
        notif = str(r.get("notificacao_metrica") or "").strip()
        linhas.append(f"[{i:03d}] {r.get('titulo', '')}")
        linhas.append(f"      Autores     : {r.get('autores') or 'Nao_Informado'}")
        linhas.append(f"      Revista     : {r.get('periodico') or 'Nao_Informado'} | Ano: {r.get('ano') or 'N/A'} | Idioma: {r.get('idioma') or 'N/A'}")
        linhas.append(f"      Metricas    : H-Index={h_str} | Quartil={q_val} | CiteScore={cs_str} | SJR={sjr_val}")
        if notif:
            linhas.append(f"      Aviso       : {notif}")
        linhas.append(f"      Plataformas : {r.get('plataformas_consolidadas') or r.get('plataforma', '')}")
        linhas.append(f"      DOI         : {r.get('doi') or 'N/A'} | ID Externo: {r.get('identificador_externo') or 'N/A'}")
        linhas.append(f"      Download    : {r.get('status_download') or 'NAO_AVALIADO'} | Arquivo: {r.get('arquivo_pdf') or '-'}")

    linhas.extend([
        "-" * 82,
        "4. RANKING DE REVISTAS POR H-INDEX",
        "-" * 82,
    ])
    rev_ordenadas = sorted(
        revistas_map.values(),
        key=lambda x: (-_to_int(x.get("h_index"), -1), str(x.get("periodico") or "")),
    )
    for idx_r, rv in enumerate(rev_ordenadas, start=1):
        h_str = _h_index_texto(rv)
        cs_raw = rv.get("citescore")
        cs_str = str(cs_raw) if cs_raw not in ("", None) else "N/D"
        linhas.append(
            f"  ({idx_r:03d}) {rv.get('periodico')} | H-Index: {h_str} | Quartil: {rv.get('quartil', 'N/D')} "
            f"| CiteScore: {cs_str} | SJR: {rv.get('sjr', 'N/D')} | Artigos: {_to_int(rv.get('qtd_artigos'), 0)}"
        )

    if excluidos:
        linhas.extend([
            "-" * 82,
            "5. ARTIGOS EXCLUIDOS",
            "-" * 82,
        ])
        for i, ex in enumerate(excluidos, start=1):
            h_ex_str = _h_index_texto(ex)
            linhas.append(
                f"  ({i:03d}) [{ex.get('plataforma')}] {ex.get('titulo')} | Revista: {ex.get('periodico') or 'Nao_Informado'} "
                f"| H-Index: {h_ex_str} | DOI: {ex.get('doi') or 'N/A'} "
                f"| Motivo: {ex.get('motivo_triagem')} | Termo: {ex.get('termo_responsavel') or '-'}"
            )

    linhas.extend(["=" * 82, "FIM DO RELATORIO", "=" * 82])
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    return txt_path


def _estilizar_cabecalho(ws: Any, num_colunas: int, cor_fundo: str = "1F4E78") -> None:
    if not HAS_OPENPYXL or ws is None:
        return
    fill = PatternFill(start_color=cor_fundo, end_color=cor_fundo, fill_type="solid")
    font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
    alinhamento = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col_idx in range(1, num_colunas + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = fill
        cell.font = font
        cell.alignment = alinhamento
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(num_colunas)}{max(1, ws.max_row)}"


def _ajustar_larguras(ws: Any, max_largura: int = 48) -> None:
    if not HAS_OPENPYXL or ws is None:
        return
    for col in ws.columns:
        col_idx = col[0].column
        if col_idx is None:
            continue
        col_letra = get_column_letter(col_idx)
        maior = len(str(col[0].value or "")) + 4
        for cell in col[1:]:
            val_str = str(cell.value or "")
            if not val_str.startswith("="):
                maior = max(maior, min(len(val_str) + 2, max_largura))
        ws.column_dimensions[col_letra].width = max(12, maior)


def _escrever_workbook_xlsx(
    xlsx_path: str,
    cfg: Any,
    stats: dict[str, Any],
    todos_registros: list[dict[str, Any]],
    aprovados: list[dict[str, Any]],
    excluidos: list[dict[str, Any]],
    historico_downloads: list[dict[str, Any]],
    stats_por_consulta: dict[tuple[str, str], dict[str, int]],
    eventos_execucao: list[dict[str, Any]],
    resumo_revistas: dict[str, dict[str, Any]] | None = None,
) -> None:
    wb = openpyxl.Workbook()

    ws_dash: Any = wb.active
    if ws_dash is None:
        ws_dash = wb.create_sheet("DASHBOARD")
    else:
        ws_dash.title = "DASHBOARD"

    ws_todos: Any = wb.create_sheet("TODOS_OS_REGISTROS")
    ws_aprov: Any = wb.create_sheet("ARTIGOS_APROVADOS")
    ws_excl: Any = wb.create_sheet("ARTIGOS_EXCLUIDOS")
    ws_down: Any = wb.create_sheet("DOWNLOADS")
    ws_str: Any = wb.create_sheet("POR_STRINGS")
    ws_plat: Any = wb.create_sheet("POR_PLATAFORMAS")
    ws_lang: Any = wb.create_sheet("POR_IDIOMAS")
    ws_ano: Any = wb.create_sheet("POR_ANO")
    ws_rev: Any = wb.create_sheet("POR_REVISTAS")
    ws_err: Any = wb.create_sheet("EXECUCAO_E_ERROS")

    # ABA 2 — TODOS_OS_REGISTROS
    cols_todos = [
        "ID interno", "Plataforma", "Plataformas consolidadas", "String de busca", "Strings consolidadas",
        "Título", "Autores", "Periódico", "H-Index revista", "Quartil revista", "CiteScore revista",
        "SJR revista", "Fonte métrica", "Notificação métrica", "Editora", "Ano", "Idioma", "Tipo de publicação",
        "DOI", "Identificador externo", "ISSN", "Acesso aberto", "URL original", "URL do PDF",
        "Data publicação", "Data recuperação", "Resumo", "Situação na triagem",
        "Motivo da exclusão ou aprovação", "Termo responsável", "Situação da deduplicação",
        "Identificador do registro original", "Critério deduplicação", "Status do download",
        "Nome do arquivo", "Tamanho do arquivo", "Hash SHA-256", "Data da tentativa",
        "Número de tentativas", "Mensagem de erro",
    ]
    ws_todos.append(cols_todos)
    for r in todos_registros:
        ws_todos.append([
            str(r.get("id_interno") or ""),
            str(r.get("plataforma_inicial") or r.get("plataforma") or ""),
            str(r.get("plataformas_consolidadas") or ""),
            str(r.get("string_busca_inicial") or r.get("string_busca") or ""),
            str(r.get("strings_consolidadas") or ""),
            str(r.get("titulo") or ""),
            str(r.get("autores") or ""),
            str(r.get("periodico") or ""),
            _h_index_celula(r),
            str(r.get("quartil_revista") or "N/D"),
            r.get("citescore_revista") if r.get("citescore_revista") not in ("", None) else "",
            str(r.get("sjr_revista") or "N/D"),
            str(r.get("fonte_metrica_revista") or ""),
            str(r.get("notificacao_metrica") or ""),
            str(r.get("editora") or ""),
            str(r.get("ano") or ""),
            str(r.get("idioma") or "") or "IDIOMA_NAO_INFORMADO",
            str(r.get("tipo") or ""),
            str(r.get("doi") or ""),
            str(r.get("identificador_externo") or ""),
            str(r.get("issn") or ""),
            str(r.get("acesso_aberto") or ""),
            str(r.get("landing_url") or ""),
            str(r.get("pdf_url") or ""),
            str(r.get("data_publicacao") or ""),
            str(r.get("data_recuperacao") or ""),
            str(r.get("resumo") or ""),
            str(r.get("situacao_triagem") or ""),
            str(r.get("motivo_triagem") or ""),
            str(r.get("termo_responsavel") or ""),
            str(r.get("status_dedup") or ""),
            str(r.get("id_registro_original") or ""),
            str(r.get("criterio_dedup") or ""),
            str(r.get("status_download") or "") or "DOWNLOAD_NAO_SOLICITADO",
            str(r.get("arquivo_pdf") or ""),
            _to_int(r.get("tamanho_pdf_bytes"), 0),
            str(r.get("sha256_pdf") or ""),
            str(r.get("data_tentativa_download") or ""),
            _to_int(r.get("tentativas_download"), 0),
            str(r.get("mensagem_download") or ""),
        ])
    _estilizar_cabecalho(ws_todos, len(cols_todos))
    _ajustar_larguras(ws_todos)

    # ABA 3 — ARTIGOS_APROVADOS
    cols_aprov = [
        "ID interno", "Título", "Autores", "Ano", "Idioma", "Tipo", "Periódico",
        "H-Index revista", "Quartil revista", "CiteScore revista", "SJR revista",
        "Notificação métrica", "Editora", "DOI", "Identificador externo", "ISSN", "Acesso aberto",
        "Plataformas de origem", "Strings de origem", "URL original", "URL do PDF",
        "Status do download", "Arquivo PDF", "Hash SHA-256", "Resumo",
    ]
    ws_aprov.append(cols_aprov)
    for r in aprovados:
        ws_aprov.append([
            str(r.get("id_interno") or ""),
            str(r.get("titulo") or ""),
            str(r.get("autores") or ""),
            str(r.get("ano") or ""),
            str(r.get("idioma") or "") or "IDIOMA_NAO_INFORMADO",
            str(r.get("tipo") or ""),
            str(r.get("periodico") or ""),
            _h_index_celula(r),
            str(r.get("quartil_revista") or "N/D"),
            r.get("citescore_revista") if r.get("citescore_revista") not in ("", None) else "",
            str(r.get("sjr_revista") or "N/D"),
            str(r.get("notificacao_metrica") or ""),
            str(r.get("editora") or ""),
            str(r.get("doi") or ""),
            str(r.get("identificador_externo") or ""),
            str(r.get("issn") or ""),
            str(r.get("acesso_aberto") or ""),
            str(r.get("plataformas_consolidadas") or ""),
            str(r.get("strings_consolidadas") or ""),
            str(r.get("landing_url") or ""),
            str(r.get("pdf_url") or ""),
            str(r.get("status_download") or ""),
            str(r.get("arquivo_pdf") or ""),
            str(r.get("sha256_pdf") or ""),
            str(r.get("resumo") or ""),
        ])
    _estilizar_cabecalho(ws_aprov, len(cols_aprov), "1E6B3A")
    _ajustar_larguras(ws_aprov)

    # ABA 4 — ARTIGOS_EXCLUIDOS
    cols_excl = [
        "ID interno", "Título", "Periódico", "H-Index revista", "Plataforma",
        "String de origem", "DOI", "Motivo da exclusão", "Termo responsável pela exclusão",
    ]
    ws_excl.append(cols_excl)
    for ex in excluidos:
        ws_excl.append([
            str(ex.get("id_interno") or ""),
            str(ex.get("titulo") or ""),
            str(ex.get("periodico") or ""),
            _h_index_celula(ex),
            str(ex.get("plataforma_inicial") or ex.get("plataforma") or ""),
            str(ex.get("string_busca_inicial") or ex.get("string_busca") or ""),
            str(ex.get("doi") or ""),
            str(ex.get("motivo_triagem") or ""),
            str(ex.get("termo_responsavel") or ""),
        ])
    _estilizar_cabecalho(ws_excl, len(cols_excl), "8B2635")
    _ajustar_larguras(ws_excl)

    # ABA 5 — DOWNLOADS
    cols_down = [
        "ID interno", "Plataforma", "String de busca", "Título", "Periódico", "H-Index revista",
        "DOI", "URL do PDF", "Tentativa", "Data e hora", "Status do download", "Arquivo PDF",
        "Tamanho (bytes)", "Hash SHA-256", "Descrição / erro",
    ]
    mapa_info_id: dict[str, tuple[str, int | str]] = {
        str(r.get("id_interno") or ""): (str(r.get("periodico") or ""), _h_index_celula(r))
        for r in todos_registros
    }
    ws_down.append(cols_down)
    for h in historico_downloads:
        per_h, h_h = mapa_info_id.get(str(h.get("id_interno") or ""), ("", ""))
        ws_down.append([
            str(h.get("id_interno") or ""),
            str(h.get("plataforma") or ""),
            str(h.get("string_busca") or ""),
            str(h.get("titulo") or ""),
            per_h,
            h_h,
            str(h.get("doi") or ""),
            str(h.get("url_pdf") or ""),
            _to_int(h.get("tentativa"), 0),
            str(h.get("data_hora") or ""),
            str(h.get("status_download") or ""),
            str(h.get("arquivo_pdf") or ""),
            _to_int(h.get("tamanho_bytes"), 0),
            str(h.get("sha256") or ""),
            str(h.get("mensagem_erro") or ""),
        ])
    _estilizar_cabecalho(ws_down, len(cols_down))
    _ajustar_larguras(ws_down)

    # ABA 6 — POR_STRINGS
    cols_str = [
        "String de busca", "Plataforma", "Resultados brutos", "Registros únicos",
        "Duplicados", "Aprovados", "Excluídos", "PDFs disponíveis", "PDFs baixados", "PDFs com falha",
    ]
    ws_str.append(cols_str)
    for (q, plat), st in stats_por_consulta.items():
        ws_str.append([
            q,
            plat,
            _to_int(st.get("brutos"), 0),
            _to_int(st.get("unicos"), 0),
            _to_int(st.get("duplicados"), 0),
            _to_int(st.get("aprovados"), 0),
            _to_int(st.get("excluidos"), 0),
            _to_int(st.get("pdfs_disponiveis"), 0),
            _to_int(st.get("pdfs_baixados"), 0),
            _to_int(st.get("pdfs_falha"), 0),
        ])
    _estilizar_cabecalho(ws_str, len(cols_str))
    _ajustar_larguras(ws_str)

    # ABA 7 — POR_PLATAFORMAS
    cols_plat = [
        "Plataforma", "Total recuperado", "Total único", "Total duplicado",
        "Total aprovado", "Total excluído", "PDFs disponíveis", "Downloads realizados",
        "Downloads bem-sucedidos", "Downloads malsucedidos", "Taxa de sucesso",
    ]
    ws_plat.append(cols_plat)
    plataformas_lista = list(cfg.plataformas) if getattr(cfg, "plataformas", None) else []
    for r in todos_registros:
        p = str(r.get("plataforma_inicial") or r.get("plataforma") or "")
        if p and p not in plataformas_lista:
            plataformas_lista.append(p)

    for idx_p, plat in enumerate(plataformas_lista, start=2):
        regs_p = [r for r in todos_registros if str(r.get("plataforma_inicial") or r.get("plataforma") or "") == plat]
        t_rec = len(regs_p)
        t_uni = sum(1 for r in regs_p if r.get("status_dedup") == "UNICO")
        t_dup = sum(1 for r in regs_p if r.get("status_dedup") == "DUPLICADO")
        t_apr = sum(1 for r in regs_p if r.get("situacao_triagem") == "APROVADO")
        t_exc = sum(1 for r in regs_p if r.get("situacao_triagem") == "EXCLUIDO")
        t_pdf_disp = sum(1 for r in regs_p if r.get("pdf_url"))
        t_ok = sum(1 for r in regs_p if r.get("status_download") in {"BAIXADO_COM_SUCESSO", "JA_EXISTIA_VALIDADO"})
        t_fail = sum(1 for r in regs_p if r.get("status_download") not in {"", "BAIXADO_COM_SUCESSO", "JA_EXISTIA_VALIDADO", "SEM_LINK_PDF", "DOWNLOAD_NAO_SOLICITADO"})
        t_real = t_ok + t_fail
        ws_plat.append([
            plat,
            t_rec,
            t_uni,
            t_dup,
            t_apr,
            t_exc,
            t_pdf_disp,
            t_real,
            t_ok,
            t_fail,
            f"=IF(H{idx_p}>0, I{idx_p}/H{idx_p}, 0)",
        ])
        ws_plat.cell(row=idx_p, column=11).number_format = "0.0%"
    _estilizar_cabecalho(ws_plat, len(cols_plat))
    _ajustar_larguras(ws_plat)

    # ABA 8 — POR_IDIOMAS
    cols_lang = [
        "Código do idioma", "Nome do idioma", "Quantidade de registros",
        "Percentual sobre o total", "Quantidade de artigos aprovados", "Quantidade de PDFs baixados",
    ]
    ws_lang.append(cols_lang)
    mapa_idiomas: dict[str, list[dict[str, Any]]] = {}
    for r in todos_registros:
        cod = str(r.get("idioma") or "").strip().lower() or "IDIOMA_NAO_INFORMADO"
        mapa_idiomas.setdefault(cod, []).append(r)

    total_regs_base = max(1, len(todos_registros))
    for idx_l, (cod, lista_l) in enumerate(sorted(mapa_idiomas.items(), key=lambda x: -len(x[1])), start=2):
        nome_l = "Não informado" if cod == "IDIOMA_NAO_INFORMADO" else NOMES_IDIOMAS.get(cod, cod.upper())
        qtd_l = len(lista_l)
        apr_l = sum(1 for x in lista_l if x.get("situacao_triagem") == "APROVADO")
        pdf_l = sum(1 for x in lista_l if x.get("status_download") in {"BAIXADO_COM_SUCESSO", "JA_EXISTIA_VALIDADO"})
        ws_lang.append([
            cod,
            nome_l,
            qtd_l,
            f"=C{idx_l}/{total_regs_base}",
            apr_l,
            pdf_l,
        ])
        ws_lang.cell(row=idx_l, column=4).number_format = "0.0%"
    _estilizar_cabecalho(ws_lang, len(cols_lang))
    _ajustar_larguras(ws_lang)

    # ABA 9 — POR_ANO
    cols_ano = ["Ano de publicação", "Total de registros", "Artigos únicos", "Artigos aprovados", "PDFs baixados"]
    ws_ano.append(cols_ano)
    mapa_anos: dict[str, list[dict[str, Any]]] = {}
    for r in todos_registros:
        ano_c = str(r.get("ano") or "").strip() or "SEM_ANO"
        mapa_anos.setdefault(ano_c, []).append(r)
    for ano_c, lista_a in sorted(mapa_anos.items()):
        ws_ano.append([
            ano_c,
            len(lista_a),
            sum(1 for x in lista_a if x.get("status_dedup") == "UNICO"),
            sum(1 for x in lista_a if x.get("situacao_triagem") == "APROVADO"),
            sum(1 for x in lista_a if x.get("status_download") in {"BAIXADO_COM_SUCESSO", "JA_EXISTIA_VALIDADO"}),
        ])
    _estilizar_cabecalho(ws_ano, len(cols_ano))
    _ajustar_larguras(ws_ano)

    # ABA 10 — POR_REVISTAS
    cols_rev = [
        "Periódico", "ISSN", "H-Index", "Quartil", "CiteScore",
        "SJR", "Quantidade de artigos", "Fonte da métrica", "Notificação",
    ]
    ws_rev.append(cols_rev)
    rev_dict = resumo_revistas or {}
    for rv in sorted(
        rev_dict.values(),
        key=lambda x: (-_to_int(x.get("h_index"), -1), str(x.get("periodico") or "")),
    ):
        cs_v = rv.get("citescore")
        ws_rev.append([
            str(rv.get("periodico") or ""),
            str(rv.get("issn") or ""),
            _h_index_celula(rv),
            str(rv.get("quartil") or "N/D"),
            cs_v if cs_v not in ("", None) else "",
            str(rv.get("sjr") or "N/D"),
            _to_int(rv.get("qtd_artigos"), 0),
            str(rv.get("fonte_metrica") or ""),
            str(rv.get("notificacao") or ""),
        ])
    _estilizar_cabecalho(ws_rev, len(cols_rev), "2B4C7E")
    _ajustar_larguras(ws_rev)

    # ABA 11 — EXECUCAO_E_ERROS
    cols_err = [
        "Data e hora", "Plataforma", "String", "Tipo de operação",
        "Código HTTP", "Número da tentativa", "Tempo de espera", "Resultado", "Descrição do erro",
    ]
    ws_err.append(cols_err)
    for ev in eventos_execucao:
        ws_err.append([
            str(ev.get("data_hora") or ""),
            str(ev.get("plataforma") or ""),
            str(ev.get("string_busca") or ""),
            str(ev.get("operacao") or ""),
            str(ev.get("codigo_http") or ""),
            _to_int(ev.get("tentativa"), 0),
            _to_float(ev.get("tempo_espera"), 0.0),
            str(ev.get("resultado") or ""),
            str(ev.get("descricao_erro") or ""),
        ])
    _estilizar_cabecalho(ws_err, len(cols_err))
    _ajustar_larguras(ws_err)

    # Formatação condicional para Status do download em TODOS_OS_REGISTROS (coluna AH = 34)
    if len(todos_registros) > 0:
        faixa_status = f"AH2:AH{len(todos_registros) + 1}"
        ws_todos.conditional_formatting.add(
            faixa_status,
            CellIsRule(operator="equal", formula=['"BAIXADO_COM_SUCESSO"'], fill=PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")),
        )
        ws_todos.conditional_formatting.add(
            faixa_status,
            CellIsRule(operator="equal", formula=['"JA_EXISTIA_VALIDADO"'], fill=PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")),
        )
        ws_todos.conditional_formatting.add(
            faixa_status,
            CellIsRule(operator="equal", formula=['"HTTP_403"'], fill=PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")),
        )

    # ABA 1 — DASHBOARD (sem gráficos, mantendo todos os KPIs e fórmulas)
    ws_dash["A1"] = "PESQUISANATOR XP — DASHBOARD EXECUTIVO DE COLETA BIBLIOGRÁFICA"
    ws_dash["A1"].font = Font(name="Tahoma", size=13, bold=True, color="FFFFFF")
    ws_dash["A1"].fill = PatternFill(start_color="0054E3", end_color="0054E3", fill_type="solid")
    ws_dash.merge_cells("A1:D1")
    ws_dash.row_dimensions[1].height = 28

    ws_dash["A3"] = "Indicador"
    ws_dash["B3"] = "Valor"
    ws_dash["A3"].font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
    ws_dash["B3"].font = Font(name="Tahoma", size=10, bold=True, color="FFFFFF")
    ws_dash["A3"].fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    ws_dash["B3"].fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")

    max_row_todos = max(2, len(todos_registros) + 1)
    kpis: list[tuple[str, int | str]] = [
        ("Total de registros brutos recuperados", f"=COUNTA(TODOS_OS_REGISTROS!A2:A{max_row_todos})" if todos_registros else 0),
        ("Total de artigos únicos", f'=COUNTIF(TODOS_OS_REGISTROS!AE2:AE{max_row_todos},"UNICO")' if todos_registros else 0),
        ("Total de duplicados", f'=COUNTIF(TODOS_OS_REGISTROS!AE2:AE{max_row_todos},"DUPLICADO")' if todos_registros else 0),
        ("Total de artigos aprovados", f'=COUNTIF(TODOS_OS_REGISTROS!AB2:AB{max_row_todos},"APROVADO")' if todos_registros else 0),
        ("Total de artigos excluídos", f'=COUNTIF(TODOS_OS_REGISTROS!AB2:AB{max_row_todos},"EXCLUIDO")' if todos_registros else 0),
        ("Total de artigos ignorados", f'=COUNTIF(TODOS_OS_REGISTROS!AB2:AB{max_row_todos},"IGNORADO_DUPLICADO")' if todos_registros else 0),
        ("Total de PDFs disponíveis", _to_int(stats.get("pdfs_disponiveis"), 0)),
        ("Total de PDFs baixados", _to_int(stats.get("pdfs_baixados"), 0)),
        ("Total de PDFs já existentes", _to_int(stats.get("pdfs_ja_existiam"), 0)),
        ("Total de PDFs bloqueados", sum(1 for r in todos_registros if r.get("status_download") in {"ACESSO_RESTRITO", "HTTP_403"})),
        ("Total de PDFs sem link", sum(1 for r in todos_registros if r.get("status_download") == "SEM_LINK_PDF")),
        ("Total de falhas", _to_int(stats.get("pdfs_falha"), 0)),
        ("Taxa de sucesso dos downloads", "=IF((B11+B12+B15)>0, (B11+B12)/(B11+B12+B15), 0)"),
        ("Quantidade de plataformas consultadas", _to_int(stats.get("plataformas_consultadas"), 0)),
        ("Quantidade de strings executadas", _to_int(stats.get("strings_executadas"), 0)),
        ("Quantidade de periódicos avaliados", len(rev_dict)),
    ]

    for offset, (rotulo, valor) in enumerate(kpis, start=4):
        ws_dash.cell(row=offset, column=1, value=rotulo).font = Font(name="Tahoma", size=9, bold=True)
        c_val = ws_dash.cell(row=offset, column=2, value=valor)
        c_val.font = Font(name="Tahoma", size=9)
        if isinstance(valor, str) and valor.startswith("=IF("):
            c_val.number_format = "0.0%"
        else:
            c_val.number_format = "#,##0"

    _ajustar_larguras(ws_dash, max_largura=48)
    wb.save(xlsx_path)


def exportar_xlsx(
    pasta_saida: str,
    cfg: Any,
    stats: dict[str, Any],
    todos_registros: list[dict[str, Any]],
    aprovados: list[dict[str, Any]],
    excluidos: list[dict[str, Any]],
    historico_downloads: list[dict[str, Any]],
    stats_por_consulta: dict[tuple[str, str], dict[str, int]],
    eventos_execucao: list[dict[str, Any]],
    on_log: Callable[[str], None] | None = None,
    resumo_revistas: dict[str, dict[str, Any]] | None = None,
) -> str:
    if not HAS_OPENPYXL:
        raise RuntimeError("Biblioteca openpyxl não disponível.")

    xlsx_path = os.path.join(pasta_saida, "relatorio_coleta.xlsx")
    _escrever_workbook_xlsx(
        xlsx_path=xlsx_path,
        cfg=cfg,
        stats=stats,
        todos_registros=todos_registros,
        aprovados=aprovados,
        excluidos=excluidos,
        historico_downloads=historico_downloads,
        stats_por_consulta=stats_por_consulta,
        eventos_execucao=eventos_execucao,
        resumo_revistas=resumo_revistas,
    )
    if on_log:
        on_log(f"[XLSX] Planilha completa gerada em: {xlsx_path}")
    return xlsx_path
