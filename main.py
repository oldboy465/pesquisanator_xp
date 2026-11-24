from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from conectores import (
    DEFAULT_DELAYS,
    buscar_arxiv,
    buscar_crossref,
    buscar_doaj,
    buscar_europepmc,
    buscar_openalex,
    criar_sessao_http,
)
from interface import WindowsXPCollectorGUI
from metricas_revista import enriquecer_metricas_revistas
from processamento import (
    ControleExecucao,
    baixar_pdfs_seguro,
    carregar_checkpoint,
    deduplicar_com_proveniencia,
    filtrar_registros,
    salvar_checkpoint,
)
from relatorios import (
    HAS_OPENPYXL,
    exportar_csv,
    exportar_bibtex,
    exportar_xlsx,
    gerar_relatorio_txt,
)

APP_NAME = "Pesquisanator XP"
APP_VERSION = "v3.1 Luna SP3"
AUTORES = "Philipe Lima & Colaboradores"
CONTATO_GITHUB = "GitHub: oldboy465 | ph465drive@gmail.com"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".pesquisanator_xp_config.json")


@dataclass
class ConfiguracaoExecucao:
    queries: list[str]
    plataformas: list[str]
    ano_ini: int = 2020
    ano_fim: int = 2026
    idiomas: list[str] | None = None
    limite_por_string: int = 50
    limite_total: int = 0
    timeout: int = 30
    max_tentativas: int = 4
    email: str = "pesquisador@exemplo.com"
    openalex_api_key: str = ""
    apenas_periodicos: bool = True
    apenas_oa: bool = True
    baixar_pdfs: bool = True
    retomar_checkpoint: bool = True
    termos_incl: list[str] | None = None
    termos_excl: list[str] | None = None
    pasta_saida: str = os.path.abspath("coleta_pesquisanator_xp")
    delays: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.idiomas is None:
            self.idiomas = ["pt", "en"]
        if self.termos_incl is None:
            self.termos_incl = []
        if self.termos_excl is None:
            self.termos_excl = []
        if self.delays is None:
            self.delays = dict(DEFAULT_DELAYS)


def carregar_configuracao_persistida() -> dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def salvar_configuracao_persistida(cfg: ConfiguracaoExecucao) -> None:
    payload = asdict(cfg)
    payload["openalex_api_key"] = ""
    payload.pop("queries", None)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class OrquestradorColeta:
    def __init__(self, event_queue: queue.Queue) -> None:
        self.event_queue = event_queue
        self.controle = ControleExecucao()
        self._thread: threading.Thread | None = None

    def emit(self, tipo: str, payload: Any) -> None:
        self.event_queue.put({"tipo": tipo, "payload": payload})

    def log(self, mensagem: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.emit("log", f"[{stamp}] {mensagem}")

    def iniciar(self, cfg: ConfiguracaoExecucao) -> None:
        if self._thread and self._thread.is_alive():
            self.log("Já existe uma execução em andamento.")
            return
        self.controle.resetar()
        self._thread = threading.Thread(target=self._executar, args=(cfg,), daemon=True)
        self._thread.start()

    def pausar(self) -> None:
        self.controle.pausar()
        self.log("Execução pausada pelo usuário.")
        self.emit("estado", "PAUSADO")

    def retomar(self) -> None:
        self.controle.retomar()
        self.log("Execução retomada.")
        self.emit("estado", "EXECUTANDO")

    def cancelar(self) -> None:
        self.controle.cancelar()
        self.log("Cancelamento solicitado. Finalizando operações com segurança...")
        self.emit("estado", "CANCELANDO")

    def _executar(self, cfg: ConfiguracaoExecucao) -> None:
        inicio_ts = time.monotonic()
        eventos_execucao: list[dict[str, Any]] = []
        historico_downloads: list[dict[str, Any]] = []
        stats_por_consulta: dict[tuple[str, str], dict[str, int]] = {}
        resumo_revistas: dict[str, dict[str, Any]] = {}
        erros_count = 0

        idiomas_cfg = cfg.idiomas or []
        termos_incl_cfg = cfg.termos_incl or []
        termos_excl_cfg = cfg.termos_excl or []
        delays_cfg = cfg.delays or dict(DEFAULT_DELAYS)

        def registrar_evento(ev: dict[str, Any]) -> None:
            nonlocal erros_count
            eventos_execucao.append(ev)
            if ev.get("resultado") in {"ERRO", "RATE_LIMIT_429", "FALHA_APOS_TENTATIVAS"}:
                erros_count += 1
                self.emit("metricas", {"erros": erros_count})

        try:
            self.emit("estado", "EXECUTANDO")
            os.makedirs(cfg.pasta_saida, exist_ok=True)
            salvar_configuracao_persistida(cfg)

            checkpoint_path = os.path.join(cfg.pasta_saida, "checkpoint_coleta.json")
            checkpoint = carregar_checkpoint(checkpoint_path) if cfg.retomar_checkpoint else {
                "consultas_concluidas": [],
                "registros_brutos": [],
            }
            concluidas_set = set(checkpoint.get("consultas_concluidas", []))
            brutos: list[dict[str, Any]] = list(checkpoint.get("registros_brutos", []))

            for q in cfg.queries:
                for plat in cfg.plataformas:
                    stats_por_consulta[(q, plat)] = {
                        "brutos": 0,
                        "unicos": 0,
                        "duplicados": 0,
                        "aprovados": 0,
                        "excluidos": 0,
                        "pdfs_disponiveis": 0,
                        "pdfs_baixados": 0,
                        "pdfs_falha": 0,
                    }

            for r in brutos:
                chave_qp = (r.get("string_busca", ""), r.get("plataforma", ""))
                if chave_qp in stats_por_consulta:
                    stats_por_consulta[chave_qp]["brutos"] += 1

            total_etapas = max(1, len(cfg.queries) * len(cfg.plataformas))
            etapa_atual = 0

            session = criar_sessao_http(cfg.email)
            try:
                for q in cfg.queries:
                    for plat in cfg.plataformas:
                        if self.controle.esta_cancelado():
                            break
                        self.controle.aguardar_se_pausado()
                        if self.controle.esta_cancelado():
                            break

                        etapa_atual += 1
                        pct = int((etapa_atual / total_etapas) * 100)
                        self.emit("progresso_geral", {"valor": pct, "texto": f"{plat}: {q[:35]}"})
                        self.emit("progresso_plataforma", {"plataforma": plat, "percentual": pct, "status": "Consultando"})

                        assinatura_consulta = f"{plat}||{q}||{cfg.ano_ini}-{cfg.ano_fim}||{cfg.limite_por_string}"
                        if assinatura_consulta in concluidas_set:
                            self.log(f"[{plat}] Consulta já concluída no checkpoint. Pulando: '{q}'")
                            self.emit("progresso_plataforma", {"plataforma": plat, "percentual": 100, "status": "Checkpoint"})
                            continue

                        if cfg.limite_total > 0 and len(brutos) >= cfg.limite_total:
                            self.log(f"Limite total de {cfg.limite_total} registros atingido.")
                            break

                        restante_total = (
                            max(0, cfg.limite_total - len(brutos))
                            if cfg.limite_total > 0
                            else cfg.limite_por_string
                        )
                        limite_efetivo = min(cfg.limite_por_string, restante_total) if cfg.limite_total > 0 else cfg.limite_por_string
                        if limite_efetivo <= 0:
                            break

                        delay_plat = float(delays_cfg.get(plat, DEFAULT_DELAYS.get(plat, 0.5)))
                        self.log(f"[{plat}] Buscando '{q}' (limite={limite_efetivo})...")

                        try:
                            if plat == "OpenAlex":
                                itens = buscar_openalex(
                                    session=session,
                                    query=q,
                                    ano_ini=cfg.ano_ini,
                                    ano_fim=cfg.ano_fim,
                                    idiomas=idiomas_cfg,
                                    apenas_periodicos=cfg.apenas_periodicos,
                                    apenas_oa=cfg.apenas_oa,
                                    email=cfg.email,
                                    api_key=cfg.openalex_api_key,
                                    limite=limite_efetivo,
                                    timeout=cfg.timeout,
                                    max_tentativas=cfg.max_tentativas,
                                    delay_base=delay_plat,
                                    controle=self.controle,
                                    on_evento=registrar_evento,
                                    on_log=self.log,
                                )
                            elif plat == "CrossRef":
                                itens = buscar_crossref(
                                    session=session,
                                    query=q,
                                    ano_ini=cfg.ano_ini,
                                    ano_fim=cfg.ano_fim,
                                    idiomas=idiomas_cfg,
                                    apenas_periodicos=cfg.apenas_periodicos,
                                    email=cfg.email,
                                    limite=limite_efetivo,
                                    timeout=cfg.timeout,
                                    max_tentativas=cfg.max_tentativas,
                                    delay_base=delay_plat,
                                    controle=self.controle,
                                    on_evento=registrar_evento,
                                    on_log=self.log,
                                )
                            elif plat == "EuropePMC":
                                itens = buscar_europepmc(
                                    session=session,
                                    query=q,
                                    ano_ini=cfg.ano_ini,
                                    ano_fim=cfg.ano_fim,
                                    idiomas=idiomas_cfg,
                                    apenas_oa=cfg.apenas_oa,
                                    email=cfg.email,
                                    limite=limite_efetivo,
                                    timeout=cfg.timeout,
                                    max_tentativas=cfg.max_tentativas,
                                    delay_base=delay_plat,
                                    controle=self.controle,
                                    on_evento=registrar_evento,
                                    on_log=self.log,
                                )
                            elif plat == "DOAJ":
                                itens = buscar_doaj(
                                    session=session,
                                    query=q,
                                    ano_ini=cfg.ano_ini,
                                    ano_fim=cfg.ano_fim,
                                    email=cfg.email,
                                    limite=limite_efetivo,
                                    timeout=cfg.timeout,
                                    max_tentativas=cfg.max_tentativas,
                                    delay_base=delay_plat,
                                    controle=self.controle,
                                    on_evento=registrar_evento,
                                    on_log=self.log,
                                )
                            elif plat == "arXiv":
                                itens = buscar_arxiv(
                                    session=session,
                                    query=q,
                                    ano_ini=cfg.ano_ini,
                                    ano_fim=cfg.ano_fim,
                                    email=cfg.email,
                                    limite=limite_efetivo,
                                    timeout=cfg.timeout,
                                    max_tentativas=cfg.max_tentativas,
                                    delay_base=delay_plat,
                                    controle=self.controle,
                                    on_evento=registrar_evento,
                                    on_log=self.log,
                                )
                            else:
                                itens = []

                            brutos.extend(itens)
                            stats_por_consulta[(q, plat)]["brutos"] += len(itens)
                            concluidas_set.add(assinatura_consulta)
                            salvar_checkpoint(
                                checkpoint_path,
                                sorted(concluidas_set),
                                brutos,
                            )
                            self.emit("metricas", {"brutos": len(brutos)})
                            self.emit("progresso_plataforma", {"plataforma": plat, "percentual": 100, "status": f"{len(itens)} itens"})
                        except Exception as exc:
                            self.log(f"[{plat}] Falha isolada em '{q}': {type(exc).__name__}: {exc}")
                            self.emit("progresso_plataforma", {"plataforma": plat, "percentual": 100, "status": "Erro"})
                            registrar_evento({
                                "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "plataforma": plat,
                                "string_busca": q,
                                "operacao": "BUSCA",
                                "codigo_http": "",
                                "tentativa": cfg.max_tentativas,
                                "tempo_espera": 0.0,
                                "resultado": "ERRO",
                                "descricao_erro": f"{type(exc).__name__}: {exc}",
                            })

                    if cfg.limite_total > 0 and len(brutos) >= cfg.limite_total:
                        break
            finally:
                session.close()

            self.log(f"Consolidando {len(brutos)} registros brutos e aplicando deduplicação...")
            todos_registros, unicos_registros = deduplicar_com_proveniencia(brutos)

            self.log("Aplicando filtros locais e registrando triagem...")
            aprovados, excluidos, ignorados = filtrar_registros(
                todos_registros=todos_registros,
                unicos_registros=unicos_registros,
                termos_incl=termos_incl_cfg,
                termos_excl=termos_excl_cfg,
            )

            if not self.controle.esta_cancelado():
                self.log("Resolvendo métricas de periódicos (H-Index, CiteScore, SJR, Quartil)...")
                resumo_revistas = enriquecer_metricas_revistas(
                    registros=todos_registros,
                    pasta_saida=cfg.pasta_saida,
                    email=cfg.email,
                    api_key=cfg.openalex_api_key,
                    timeout=cfg.timeout,
                    max_tentativas=min(3, cfg.max_tentativas),
                    controle=self.controle,
                    on_evento=registrar_evento,
                    on_log=self.log,
                )

            for r in todos_registros:
                chave_qp = (r.get("string_busca_inicial", r.get("string_busca", "")), r.get("plataforma_inicial", r.get("plataforma", "")))
                if chave_qp in stats_por_consulta:
                    st = stats_por_consulta[chave_qp]
                    if r.get("status_dedup") == "UNICO":
                        st["unicos"] += 1
                    else:
                        st["duplicados"] += 1
                    if r.get("situacao_triagem") == "APROVADO":
                        st["aprovados"] += 1
                    elif r.get("situacao_triagem") == "EXCLUIDO":
                        st["excluidos"] += 1
                    if r.get("pdf_url"):
                        st["pdfs_disponiveis"] += 1

            pdfs_baixados = 0
            pdfs_ja_existiam = 0
            pdfs_falha = 0

            if cfg.baixar_pdfs and not self.controle.esta_cancelado():
                self.log("Iniciando motor de download de PDFs...")
                pasta_pdfs = os.path.join(cfg.pasta_saida, "pdfs_baixados")
                alvos_pdf = [r for r in todos_registros if r.get("situacao_triagem") == "APROVADO"]
                pdfs_baixados, pdfs_ja_existiam, pdfs_falha, historico_downloads = baixar_pdfs_seguro(
                    registros=alvos_pdf,
                    pasta_pdfs=pasta_pdfs,
                    email=cfg.email,
                    timeout=cfg.timeout,
                    max_tentativas=cfg.max_tentativas,
                    controle=self.controle,
                    on_log=self.log,
                    on_progresso=lambda atual, total, msg: self.emit(
                        "progresso_download",
                        {"atual": atual, "total": max(1, total), "mensagem": msg},
                    ),
                    on_metrica_pdf=lambda baixados_total: self.emit("metricas", {"pdfs": baixados_total}),
                )
            else:
                for r in todos_registros:
                    r["status_download"] = "DOWNLOAD_CANCELADO" if self.controle.esta_cancelado() else "DOWNLOAD_NAO_SOLICITADO"
                    r["mensagem_download"] = "Execução cancelada antes do download." if self.controle.esta_cancelado() else "Download não solicitado na configuração."

            for h in historico_downloads:
                chave_qp = (h.get("string_busca", ""), h.get("plataforma", ""))
                if chave_qp in stats_por_consulta:
                    if h.get("status_download") == "BAIXADO_COM_SUCESSO":
                        stats_por_consulta[chave_qp]["pdfs_baixados"] += 1
                    elif h.get("status_download") not in {"JA_EXISTIA_VALIDADO", "SEM_LINK_PDF", "DOWNLOAD_NAO_SOLICITADO"}:
                        stats_por_consulta[chave_qp]["pdfs_falha"] += 1

            duracao_seg = round(time.monotonic() - inicio_ts, 2)
            resumo_stats = {
                "total_brutos": len(brutos),
                "total_unicos": len(unicos_registros),
                "total_duplicados": sum(1 for r in todos_registros if r.get("status_dedup") == "DUPLICADO"),
                "total_aprovados": len(aprovados),
                "total_excluidos": len(excluidos),
                "total_ignorados": len(ignorados),
                "pdfs_disponiveis": sum(1 for r in todos_registros if r.get("pdf_url")),
                "pdfs_baixados": pdfs_baixados,
                "pdfs_ja_existiam": pdfs_ja_existiam,
                "pdfs_falha": pdfs_falha,
                "plataformas_consultadas": len(cfg.plataformas),
                "strings_executadas": len(cfg.queries),
                "duracao_segundos": duracao_seg,
            }

            csv_path = exportar_csv(todos_registros, cfg.pasta_saida, self.log)
            bib_path = exportar_bibtex(aprovados if aprovados else unicos_registros, cfg.pasta_saida, self.log)
            txt_path = gerar_relatorio_txt(
                pasta_saida=cfg.pasta_saida,
                cfg=cfg,
                stats=resumo_stats,
                todos_registros=todos_registros,
                aprovados=aprovados,
                excluidos=excluidos,
                ignorados=ignorados,
                stats_por_consulta=stats_por_consulta,
                eventos_execucao=eventos_execucao,
                autores=AUTORES,
                contato=CONTATO_GITHUB,
                resumo_revistas=resumo_revistas,
            )

            xlsx_path = ""
            if not HAS_OPENPYXL:
                self.log("[AVISO XLSX] Biblioteca 'openpyxl' não instalada. Execute: pip install openpyxl")
            else:
                xlsx_path = exportar_xlsx(
                    pasta_saida=cfg.pasta_saida,
                    cfg=cfg,
                    stats=resumo_stats,
                    todos_registros=todos_registros,
                    aprovados=aprovados,
                    excluidos=excluidos,
                    historico_downloads=historico_downloads,
                    stats_por_consulta=stats_por_consulta,
                    eventos_execucao=eventos_execucao,
                    on_log=self.log,
                    resumo_revistas=resumo_revistas,
                )

            estado_final = "CANCELADO" if self.controle.esta_cancelado() else "CONCLUIDO"
            self.emit("concluido", {
                "estado": estado_final,
                "csv_path": csv_path,
                "bib_path": bib_path,
                "txt_path": txt_path,
                "xlsx_path": xlsx_path,
                "stats": resumo_stats,
            })
            self.log(f"Processo finalizado ({estado_final}) em {duracao_seg}s.")
        except Exception as exc:
            self.log(f"[ERRO CRÍTICO] {type(exc).__name__}: {exc}")
            self.emit("concluido", {"estado": "ERRO", "xlsx_path": ""})
        finally:
            self.emit("estado", "PRONTO")


def main() -> None:
    root = tk.Tk()
    event_queue: queue.Queue = queue.Queue()
    orquestrador = OrquestradorColeta(event_queue)
    app = WindowsXPCollectorGUI(
        root=root,
        event_queue=event_queue,
        orquestrador=orquestrador,
        config_inicial=carregar_configuracao_persistida(),
        app_name=APP_NAME,
        app_version=APP_VERSION,
        autores=AUTORES,
        contato=CONTATO_GITHUB,
    )
    root.protocol("WM_DELETE_WINDOW", app.ao_fechar)
    root.mainloop()


if __name__ == "__main__":
    main()