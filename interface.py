from __future__ import annotations

import os
import queue
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from conectores import DEFAULT_DELAYS

if TYPE_CHECKING:
    from main import OrquestradorColeta


class BarraProgressoXP(tk.Canvas):
    def __init__(self, parent: tk.Widget, height: int = 16, cor_bloco: str = "#0A246A", **kwargs: Any) -> None:
        super().__init__(
            parent,
            height=height,
            bg="#FFFFFF",
            highlightthickness=0,
            relief="sunken",
            bd=2,
            **kwargs,
        )
        self._valor = 0.0
        self._cor_bloco = cor_bloco
        self.bind("<Configure>", lambda _e: self._desenhar())

    def set_valor(self, percentual: float) -> None:
        self._valor = max(0.0, min(100.0, float(percentual)))
        self._desenhar()

    def _desenhar(self) -> None:
        self.delete("all")
        largura = max(1, self.winfo_width() - 4)
        altura = max(1, self.winfo_height() - 4)
        if self._valor <= 0:
            return
        preenchido = int((self._valor / 100.0) * largura)
        x = 2
        bloco_w = 8
        espaco = 2
        while x < 2 + preenchido:
            fim_x = min(2 + preenchido, x + bloco_w)
            self.create_rectangle(x, 2, fim_x, 2 + altura, fill=self._cor_bloco, outline="")
            x += bloco_w + espaco


class WindowsXPCollectorGUI:
    def __init__(
        self,
        root: tk.Tk,
        event_queue: queue.Queue,
        orquestrador: OrquestradorColeta,
        config_inicial: dict[str, Any],
        app_name: str,
        app_version: str,
        autores: str,
        contato: str,
    ) -> None:
        self.root = root
        self.event_queue = event_queue
        self.orquestrador = orquestrador
        self.cfg_inicial = config_inicial or {}
        self.app_name = app_name
        self.app_version = app_version
        self.autores = autores
        self.contato = contato

        self.xp_bg = "#ECE9D8"
        self.xp_panel = "#F5F4EA"
        self.xp_blue_left = "#0054E3"
        self.xp_blue_right = "#245EDC"
        self.xp_btn_face = "#D4D0C8"
        self.xp_text = "#000000"

        self.f_ui = ("Tahoma", 9)
        self.f_bold = ("Tahoma", 9, "bold")
        self.f_title = ("Tahoma", 10, "bold")
        self.f_console = ("Consolas", 9)

        self._executando = False
        self._pausado = False
        self._inicio_execucao: float | None = None
        self._tempo_acumulado = 0.0
        self._ultimo_xlsx = ""
        self._barras_plat: dict[str, tuple[BarraProgressoXP, tk.Label]] = {}

        self.root.title(f"{self.app_name} — {self.app_version}")
        self.root.geometry("1040x760")
        self.root.minsize(900, 660)
        self.root.configure(bg=self.xp_bg)

        self._configurar_estilo_ttk()
        self._montar_menu()
        self._montar_barra_titulo_xp()
        self._montar_interface()
        self._aplicar_config_inicial()

        self._poll_eventos()
        self._atualizar_relogio()

    def _configurar_estilo_ttk(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("XP.TFrame", background=self.xp_bg)
        style.configure("XP.TLabel", background=self.xp_panel, foreground=self.xp_text, font=self.f_ui)

    def _montar_menu(self) -> None:
        menubar = tk.Menu(self.root, bg=self.xp_bg, fg=self.xp_text, font=self.f_ui)

        m_arq = tk.Menu(menubar, tearoff=0, bg=self.xp_bg, fg=self.xp_text, font=self.f_ui)
        m_arq.add_command(label="Inserir exemplo de busca", command=self.inserir_exemplo)
        m_arq.add_command(label="Limpar campos", command=self.limpar_campos)
        m_arq.add_separator()
        m_arq.add_command(label="Abrir pasta de saída", command=self.abrir_pasta_saida)
        m_arq.add_command(label="Abrir relatório XLSX", command=self.abrir_xlsx)
        m_arq.add_separator()
        m_arq.add_command(label="Sair", command=self.ao_fechar)
        menubar.add_cascade(label="Arquivo", menu=m_arq)

        m_exec = tk.Menu(menubar, tearoff=0, bg=self.xp_bg, fg=self.xp_text, font=self.f_ui)
        m_exec.add_command(label="Executar busca", command=self.disparar_coleta)
        m_exec.add_command(label="Pausar", command=self.orquestrador.pausar)
        m_exec.add_command(label="Retomar", command=self.orquestrador.retomar)
        m_exec.add_command(label="Cancelar", command=self.orquestrador.cancelar)
        menubar.add_cascade(label="Execução", menu=m_exec)

        m_ajuda = tk.Menu(menubar, tearoff=0, bg=self.xp_bg, fg=self.xp_text, font=self.f_ui)
        m_ajuda.add_command(label="Manual rápido", command=self.janela_ajuda_xp)
        m_ajuda.add_command(label="Sobre e autoria", command=self.janela_autoria_xp)
        menubar.add_cascade(label="Ajuda", menu=m_ajuda)

        self.root.config(menu=menubar)

    def _montar_barra_titulo_xp(self) -> None:
        wrap = tk.Frame(self.root, bg=self.xp_bg, bd=2, relief="raised")
        wrap.pack(fill="x", padx=4, pady=(4, 2))

        self.canvas_titulo = tk.Canvas(wrap, height=34, highlightthickness=0, bd=0)
        self.canvas_titulo.pack(fill="both", expand=True)
        self.canvas_titulo.bind("<Configure>", lambda _e: self._desenhar_gradiente_titulo())

        btns = tk.Frame(self.canvas_titulo, bg=self.xp_blue_right)
        btns.place(relx=1.0, rely=0.5, anchor="e", x=-6)

        tk.Button(
            btns,
            text="Manual",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.janela_ajuda_xp,
        ).pack(side="left", padx=2)

        tk.Button(
            btns,
            text="Sobre",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.janela_autoria_xp,
        ).pack(side="left", padx=2)

        tk.Button(
            btns,
            text="X",
            width=3,
            bg="#C75050",
            fg="#FFFFFF",
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.ao_fechar,
        ).pack(side="left", padx=(4, 0))

    def _desenhar_gradiente_titulo(self) -> None:
        c = self.canvas_titulo
        c.delete("grad")
        w = max(100, c.winfo_width())
        h = max(30, c.winfo_height())
        passos = 32
        r1, g1, b1 = 0x00, 0x54, 0xE3
        r2, g2, b2 = 0x3B, 0x8B, 0xF6
        for i in range(passos):
            t = i / max(1, passos - 1)
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            cor = f"#{r:02x}{g:02x}{b:02x}"
            x0 = int((i / passos) * w)
            x1 = int(((i + 1) / passos) * w) + 1
            c.create_rectangle(x0, 0, x1, h, fill=cor, outline="", tags="grad")

        c.create_rectangle(8, 8, 26, 26, fill="#FFFFFF", outline="#003399", width=2, tags="grad")
        c.create_text(17, 17, text="XP", fill="#003399", font=("Tahoma", 8, "bold"), tags="grad")
        c.create_text(
            34,
            h // 2,
            text=f"{self.app_name}  |  {self.app_version}",
            fill="#FFFFFF",
            font=self.f_title,
            anchor="w",
            tags="grad",
        )
        c.tag_raise("grad")

    def _montar_interface(self) -> None:
        rodape = tk.Frame(self.root, bg=self.xp_bg)
        rodape.pack(side="bottom", fill="x", padx=4, pady=(0, 4))

        self.status_var = tk.StringVar(value="Pronto. Configure as strings e parâmetros e clique em EXECUTAR BUSCA.")
        tk.Label(
            rodape,
            textvariable=self.status_var,
            bg=self.xp_bg,
            fg=self.xp_text,
            font=self.f_ui,
            anchor="w",
            relief="sunken",
            bd=2,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.relogio_var = tk.StringVar(value="Tempo: 00:00:00")
        tk.Label(
            rodape,
            textvariable=self.relogio_var,
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            width=16,
            relief="sunken",
            bd=2,
        ).pack(side="right")

        container = tk.Frame(self.root, bg=self.xp_bg)
        container.pack(fill="both", expand=True, padx=6, pady=4)

        painel_inf = tk.Frame(container, bg=self.xp_bg)
        painel_inf.pack(side="bottom", fill="x", pady=(4, 0))

        box_metricas = tk.LabelFrame(
            painel_inf,
            text=" Monitoramento e Progresso ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_metricas.pack(fill="x", pady=(0, 4))

        linha_kpi = tk.Frame(box_metricas, bg=self.xp_panel)
        linha_kpi.pack(fill="x", padx=6, pady=(4, 2))

        self.var_brutos = tk.StringVar(value="Registros: 0")
        self.var_pdfs = tk.StringVar(value="PDFs OK: 0")
        self.var_erros = tk.StringVar(value="Erros: 0")

        for var_lbl, cor_fg in [
            (self.var_brutos, "#003399"),
            (self.var_pdfs, "#1E6B3A"),
            (self.var_erros, "#8B2635"),
        ]:
            tk.Label(
                linha_kpi,
                textvariable=var_lbl,
                bg="#FFFFFF",
                fg=cor_fg,
                font=self.f_bold,
                relief="sunken",
                bd=2,
                padx=8,
                pady=2,
            ).pack(side="left", padx=(0, 8))

        self.lbl_prog_geral = tk.Label(
            linha_kpi,
            text="Progresso geral: 0%",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_ui,
        )
        self.lbl_prog_geral.pack(side="right")

        self.barra_geral = BarraProgressoXP(box_metricas, height=15, cor_bloco="#0054E3")
        self.barra_geral.pack(fill="x", padx=6, pady=(0, 3))

        linha_dl = tk.Frame(box_metricas, bg=self.xp_panel)
        linha_dl.pack(fill="x", padx=6, pady=(0, 2))
        self.lbl_prog_dl = tk.Label(
            linha_dl,
            text="Downloads de PDF: aguardando",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_ui,
        )
        self.lbl_prog_dl.pack(side="left")

        self.barra_dl = BarraProgressoXP(box_metricas, height=12, cor_bloco="#2E8B57")
        self.barra_dl.pack(fill="x", padx=6, pady=(0, 5))

        box_console = tk.LabelFrame(
            painel_inf,
            text=" Console de Execução ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_console.pack(fill="x")

        self.txt_log = tk.Text(
            box_console,
            height=7,
            bg="#0C0C0C",
            fg="#D8F3DC",
            font=self.f_console,
            relief="sunken",
            bd=2,
            state="disabled",
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True, padx=6, pady=4)

        sup = tk.Frame(container, bg=self.xp_bg)
        sup.pack(side="top", fill="both", expand=True)

        esq = tk.Frame(sup, bg=self.xp_bg)
        esq.pack(side="left", fill="both", expand=True, padx=(0, 5))

        box_q = tk.LabelFrame(
            esq,
            text=" 1. Strings de Busca (1 por linha — suporta AND, OR, NOT e aspas) ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_q.pack(fill="both", expand=True, pady=(0, 5))

        self.txt_queries = tk.Text(
            box_q,
            height=8,
            bg="#FFFFFF",
            fg=self.xp_text,
            font=self.f_console,
            relief="sunken",
            bd=2,
            wrap="word",
        )
        self.txt_queries.pack(fill="both", expand=True, padx=6, pady=(5, 3))

        barra_q = tk.Frame(box_q, bg=self.xp_panel)
        barra_q.pack(fill="x", padx=6, pady=(0, 5))
        tk.Button(
            barra_q,
            text="Inserir Exemplo",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_ui,
            relief="raised",
            bd=2,
            command=self.inserir_exemplo,
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            barra_q,
            text="Limpar Strings",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_ui,
            relief="raised",
            bd=2,
            command=lambda: self.txt_queries.delete("1.0", tk.END),
        ).pack(side="left")

        box_filtros = tk.LabelFrame(
            esq,
            text=" 2. Filtros Locais sobre Título + Resumo (separados por vírgula) ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_filtros.pack(fill="x")

        f_inc = tk.Frame(box_filtros, bg=self.xp_panel)
        f_inc.pack(fill="x", padx=6, pady=(4, 2))
        tk.Label(f_inc, text="Termos obrigatórios:", width=20, anchor="w", bg=self.xp_panel, font=self.f_ui).pack(side="left")
        self.ent_incl = tk.Entry(f_inc, bg="#FFFFFF", fg=self.xp_text, font=self.f_ui, relief="sunken", bd=2)
        self.ent_incl.pack(side="left", fill="x", expand=True)

        f_exc = tk.Frame(box_filtros, bg=self.xp_panel)
        f_exc.pack(fill="x", padx=6, pady=(0, 5))
        tk.Label(f_exc, text="Termos de exclusão:", width=20, anchor="w", bg=self.xp_panel, font=self.f_ui).pack(side="left")
        self.ent_excl = tk.Entry(f_exc, bg="#FFFFFF", fg=self.xp_text, font=self.f_ui, relief="sunken", bd=2)
        self.ent_excl.pack(side="left", fill="x", expand=True)

        dir_outer = tk.Frame(sup, bg=self.xp_bg, width=410)
        dir_outer.pack(side="right", fill="y", padx=(5, 0))
        dir_outer.pack_propagate(False)

        canvas_dir = tk.Canvas(dir_outer, bg=self.xp_bg, highlightthickness=0, bd=0)
        scroll_dir = tk.Scrollbar(dir_outer, orient="vertical", command=canvas_dir.yview)
        dir_col = tk.Frame(canvas_dir, bg=self.xp_bg)

        dir_col.bind("<Configure>", lambda _e: canvas_dir.configure(scrollregion=canvas_dir.bbox("all")))
        canvas_dir.create_window((0, 0), window=dir_col, anchor="nw", width=390)
        canvas_dir.configure(yscrollcommand=scroll_dir.set)
        canvas_dir.pack(side="left", fill="both", expand=True)
        scroll_dir.pack(side="right", fill="y")

        box_plat = tk.LabelFrame(
            dir_col,
            text=" 3. Plataformas e Progresso por Base ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_plat.pack(fill="x", pady=(0, 4))

        self.vars_plat: dict[str, tk.BooleanVar] = {
            "OpenAlex": tk.BooleanVar(value=True),
            "CrossRef": tk.BooleanVar(value=True),
            "EuropePMC": tk.BooleanVar(value=True),
            "DOAJ": tk.BooleanVar(value=True),
            "arXiv": tk.BooleanVar(value=False),
        }

        for plat_nome in ["OpenAlex", "CrossRef", "EuropePMC", "DOAJ", "arXiv"]:
            linha_p = tk.Frame(box_plat, bg=self.xp_panel)
            linha_p.pack(fill="x", padx=6, pady=1)
            tk.Checkbutton(
                linha_p,
                text=plat_nome,
                variable=self.vars_plat[plat_nome],
                bg=self.xp_panel,
                fg=self.xp_text,
                font=self.f_ui,
                width=11,
                anchor="w",
                selectcolor="#FFFFFF",
            ).pack(side="left")

            barra_p = BarraProgressoXP(linha_p, height=11, cor_bloco="#0A246A")
            barra_p.pack(side="left", fill="x", expand=True, padx=4)

            lbl_p = tk.Label(
                linha_p,
                text="Aguardando",
                bg=self.xp_panel,
                fg=self.xp_text,
                font=("Tahoma", 8),
                width=11,
                anchor="e",
            )
            lbl_p.pack(side="right")
            self._barras_plat[plat_nome] = (barra_p, lbl_p)

        box_cfg = tk.LabelFrame(
            dir_col,
            text=" 4. Parâmetros, Limites e Resiliência ",
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_bold,
            bd=2,
            relief="groove",
        )
        box_cfg.pack(fill="x", pady=(0, 4))

        def _linha_campo(parent: tk.Widget, rotulo: str, largura: int = 10) -> tk.Entry:
            f = tk.Frame(parent, bg=self.xp_panel)
            f.pack(fill="x", padx=6, pady=1)
            tk.Label(f, text=rotulo, bg=self.xp_panel, fg=self.xp_text, font=self.f_ui).pack(side="left")
            ent = tk.Entry(f, width=largura, bg="#FFFFFF", fg=self.xp_text, font=self.f_ui, relief="sunken", bd=2)
            ent.pack(side="right")
            return ent

        f_anos = tk.Frame(box_cfg, bg=self.xp_panel)
        f_anos.pack(fill="x", padx=6, pady=2)
        tk.Label(f_anos, text="Ano inicial:", bg=self.xp_panel, font=self.f_ui).pack(side="left")
        self.ent_ano_ini = tk.Entry(f_anos, width=6, bg="#FFFFFF", font=self.f_ui, relief="sunken", bd=2)
        self.ent_ano_ini.insert(0, "2020")
        self.ent_ano_ini.pack(side="left", padx=(4, 12))
        tk.Label(f_anos, text="Ano final:", bg=self.xp_panel, font=self.f_ui).pack(side="left")
        self.ent_ano_fim = tk.Entry(f_anos, width=6, bg="#FFFFFF", font=self.f_ui, relief="sunken", bd=2)
        self.ent_ano_fim.insert(0, "2026")
        self.ent_ano_fim.pack(side="left", padx=(4, 0))

        self.ent_idiomas = _linha_campo(box_cfg, "Idiomas (ex: pt,en):", 16)
        self.ent_idiomas.insert(0, "pt,en")

        self.ent_lim_string = _linha_campo(box_cfg, "Limite por string:", 8)
        self.ent_lim_string.insert(0, "50")

        self.ent_lim_total = _linha_campo(box_cfg, "Limite total (0 = sem teto):", 8)
        self.ent_lim_total.insert(0, "0")

        self.ent_timeout = _linha_campo(box_cfg, "Timeout (seg):", 8)
        self.ent_timeout.insert(0, "30")

        self.ent_tentativas = _linha_campo(box_cfg, "Máx. tentativas:", 8)
        self.ent_tentativas.insert(0, "4")

        self.ent_delay_padrao = _linha_campo(box_cfg, "Intervalo base (seg):", 8)
        self.ent_delay_padrao.insert(0, "0.40")

        self.ent_email = _linha_campo(box_cfg, "E-mail (identificação API):", 22)
        self.ent_email.insert(0, "pesquisador@exemplo.com")

        self.ent_api_key = _linha_campo(box_cfg, "Chave OpenAlex (opcional):", 22)
        self.ent_api_key.configure(show="*")

        self.var_periodicos = tk.BooleanVar(value=True)
        self.var_oa = tk.BooleanVar(value=True)
        self.var_baixar_pdf = tk.BooleanVar(value=True)
        self.var_retomar = tk.BooleanVar(value=True)

        for txt_c, var_c in [
            ("Apenas periódicos (quando suportado)", self.var_periodicos),
            ("Filtrar / priorizar acesso aberto", self.var_oa),
            ("Baixar PDFs abertos com validação", self.var_baixar_pdf),
            ("Retomar progresso salvo (checkpoint)", self.var_retomar),
        ]:
            tk.Checkbutton(
                box_cfg,
                text=txt_c,
                variable=var_c,
                bg=self.xp_panel,
                fg=self.xp_text,
                font=self.f_ui,
                selectcolor="#FFFFFF",
            ).pack(anchor="w", padx=6, pady=0)

        f_pasta = tk.Frame(dir_col, bg=self.xp_bg)
        f_pasta.pack(fill="x", pady=(2, 4))
        self.ent_pasta = tk.Entry(f_pasta, bg="#FFFFFF", fg=self.xp_text, font=self.f_ui, relief="sunken", bd=2)
        self.ent_pasta.insert(0, os.path.abspath("coleta_pesquisanator_xp"))
        self.ent_pasta.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(
            f_pasta,
            text="...",
            width=3,
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.selecionar_pasta,
        ).pack(side="right")

        f_exec = tk.Frame(dir_col, bg=self.xp_bg)
        f_exec.pack(fill="x", pady=(2, 3))

        self.btn_executar = tk.Button(
            f_exec,
            text="EXECUTAR BUSCA",
            bg="#0054E3",
            fg="#FFFFFF",
            font=self.f_bold,
            relief="raised",
            bd=3,
            command=self.disparar_coleta,
        )
        self.btn_executar.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 3))

        self.btn_pausar = tk.Button(
            f_exec,
            text="PAUSAR",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=3,
            state="disabled",
            command=self.orquestrador.pausar,
        )
        self.btn_pausar.pack(side="left", ipady=4, padx=(0, 3))

        self.btn_retomar = tk.Button(
            f_exec,
            text="RETOMAR",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=3,
            state="disabled",
            command=self.orquestrador.retomar,
        )
        self.btn_retomar.pack(side="left", ipady=4, padx=(0, 3))

        self.btn_cancelar = tk.Button(
            f_exec,
            text="CANCELAR",
            bg="#B22222",
            fg="#FFFFFF",
            font=self.f_bold,
            relief="raised",
            bd=3,
            state="disabled",
            command=self.orquestrador.cancelar,
        )
        self.btn_cancelar.pack(side="left", ipady=4)

        f_out = tk.Frame(dir_col, bg=self.xp_bg)
        f_out.pack(fill="x", pady=(0, 4))

        tk.Button(
            f_out,
            text="ABRIR PASTA",
            bg=self.xp_btn_face,
            fg=self.xp_text,
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.abrir_pasta_saida,
        ).pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 3))

        self.btn_abrir_xlsx = tk.Button(
            f_out,
            text="ABRIR XLSX",
            bg="#3C8D0D",
            fg="#FFFFFF",
            font=self.f_bold,
            relief="raised",
            bd=2,
            command=self.abrir_xlsx,
        )
        self.btn_abrir_xlsx.pack(side="right", fill="x", expand=True, ipady=3)

    def _aplicar_config_inicial(self) -> None:
        c = self.cfg_inicial
        if not c:
            return
        if "ano_ini" in c:
            self.ent_ano_ini.delete(0, tk.END)
            self.ent_ano_ini.insert(0, str(c["ano_ini"]))
        if "ano_fim" in c:
            self.ent_ano_fim.delete(0, tk.END)
            self.ent_ano_fim.insert(0, str(c["ano_fim"]))
        if "idiomas" in c and isinstance(c["idiomas"], list):
            self.ent_idiomas.delete(0, tk.END)
            self.ent_idiomas.insert(0, ",".join(c["idiomas"]))
        if "limite_por_string" in c:
            self.ent_lim_string.delete(0, tk.END)
            self.ent_lim_string.insert(0, str(c["limite_por_string"]))
        if "limite_total" in c:
            self.ent_lim_total.delete(0, tk.END)
            self.ent_lim_total.insert(0, str(c["limite_total"]))
        if "timeout" in c:
            self.ent_timeout.delete(0, tk.END)
            self.ent_timeout.insert(0, str(c["timeout"]))
        if "max_tentativas" in c:
            self.ent_tentativas.delete(0, tk.END)
            self.ent_tentativas.insert(0, str(c["max_tentativas"]))
        if "email" in c and c["email"]:
            self.ent_email.delete(0, tk.END)
            self.ent_email.insert(0, str(c["email"]))
        if "pasta_saida" in c and c["pasta_saida"]:
            self.ent_pasta.delete(0, tk.END)
            self.ent_pasta.insert(0, str(c["pasta_saida"]))
        if "plataformas" in c and isinstance(c["plataformas"], list):
            for k, var_b in self.vars_plat.items():
                var_b.set(k in c["plataformas"])
        if "apenas_periodicos" in c:
            self.var_periodicos.set(bool(c["apenas_periodicos"]))
        if "apenas_oa" in c:
            self.var_oa.set(bool(c["apenas_oa"]))
        if "baixar_pdfs" in c:
            self.var_baixar_pdf.set(bool(c["baixar_pdfs"]))
        if "retomar_checkpoint" in c:
            self.var_retomar.set(bool(c["retomar_checkpoint"]))

    def _append_log(self, msg: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.configure(state="disabled")
        self.status_var.set(msg[:135])

    def _poll_eventos(self) -> None:
        try:
            while True:
                ev = self.event_queue.get_nowait()
                tipo = ev.get("tipo")
                payload = ev.get("payload")

                if tipo == "log":
                    self._append_log(str(payload))
                elif tipo == "estado":
                    self._atualizar_estado_botoes(str(payload))
                elif tipo == "metricas" and isinstance(payload, dict):
                    if "brutos" in payload:
                        self.var_brutos.set(f"Registros: {payload['brutos']}")
                    if "pdfs" in payload:
                        self.var_pdfs.set(f"PDFs OK: {payload['pdfs']}")
                    if "erros" in payload:
                        self.var_erros.set(f"Erros: {payload['erros']}")
                elif tipo == "progresso_geral" and isinstance(payload, dict):
                    val = float(payload.get("valor", 0))
                    txt = str(payload.get("texto", ""))
                    self.barra_geral.set_valor(val)
                    self.lbl_prog_geral.configure(text=f"Progresso geral: {int(val)}% ({txt})")
                elif tipo == "progresso_plataforma" and isinstance(payload, dict):
                    plat = str(payload.get("plataforma", ""))
                    pct = float(payload.get("percentual", 0))
                    st = str(payload.get("status", ""))
                    if plat in self._barras_plat:
                        b_p, l_p = self._barras_plat[plat]
                        b_p.set_valor(pct)
                        l_p.configure(text=st[:14])
                elif tipo == "progresso_download" and isinstance(payload, dict):
                    atual = int(payload.get("atual", 0))
                    total = max(1, int(payload.get("total", 1)))
                    msg = str(payload.get("mensagem", ""))
                    pct_dl = (atual / total) * 100.0
                    self.barra_dl.set_valor(pct_dl)
                    self.lbl_prog_dl.configure(text=f"Downloads de PDF: {atual}/{total} ({msg})")
                elif tipo == "concluido" and isinstance(payload, dict):
                    self._ultimo_xlsx = str(payload.get("xlsx_path") or "")
                    self.barra_geral.set_valor(100.0)
                    self.lbl_prog_geral.configure(text=f"Progresso geral: 100% ({payload.get('estado', '')})")
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._poll_eventos)

    def _atualizar_estado_botoes(self, estado: str) -> None:
        if estado == "EXECUTANDO":
            self._executando = True
            self._pausado = False
            if self._inicio_execucao is None:
                self._inicio_execucao = time.monotonic()
            self.btn_executar.configure(state="disabled")
            self.btn_pausar.configure(state="normal")
            self.btn_retomar.configure(state="disabled")
            self.btn_cancelar.configure(state="normal")
        elif estado == "PAUSADO":
            self._pausado = True
            if self._inicio_execucao is not None:
                self._tempo_acumulado += time.monotonic() - self._inicio_execucao
                self._inicio_execucao = None
            self.btn_pausar.configure(state="disabled")
            self.btn_retomar.configure(state="normal")
            self.btn_cancelar.configure(state="normal")
        elif estado == "CANCELANDO":
            self.btn_pausar.configure(state="disabled")
            self.btn_retomar.configure(state="disabled")
            self.btn_cancelar.configure(state="disabled")
        else:
            self._executando = False
            self._pausado = False
            if self._inicio_execucao is not None:
                self._tempo_acumulado += time.monotonic() - self._inicio_execucao
                self._inicio_execucao = None
            self.btn_executar.configure(state="normal")
            self.btn_pausar.configure(state="disabled")
            self.btn_retomar.configure(state="disabled")
            self.btn_cancelar.configure(state="disabled")

    def _atualizar_relogio(self) -> None:
        decorrido = self._tempo_acumulado
        if self._inicio_execucao is not None and not self._pausado:
            decorrido += time.monotonic() - self._inicio_execucao
        total_seg = int(decorrido)
        horas = total_seg // 3600
        minutos = (total_seg % 3600) // 60
        segundos = total_seg % 60
        self.relogio_var.set(f"Tempo: {horas:02d}:{minutos:02d}:{segundos:02d}")
        self.root.after(500, self._atualizar_relogio)

    def disparar_coleta(self) -> None:
        from main import ConfiguracaoExecucao

        queries = [l.strip() for l in self.txt_queries.get("1.0", tk.END).splitlines() if l.strip()]
        if not queries:
            messagebox.showwarning("Atenção", "Insira pelo menos uma string de busca.")
            return

        plataformas = [nome for nome, var in self.vars_plat.items() if var.get()]
        if not plataformas:
            messagebox.showwarning("Atenção", "Selecione ao menos uma plataforma.")
            return

        try:
            ano_ini = int(self.ent_ano_ini.get().strip())
            ano_fim = int(self.ent_ano_fim.get().strip())
            lim_string = int(self.ent_lim_string.get().strip())
            lim_total = int(self.ent_lim_total.get().strip())
            timeout = int(self.ent_timeout.get().strip())
            tentativas = int(self.ent_tentativas.get().strip())
            delay_base = float(self.ent_delay_padrao.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showerror("Parâmetros inválidos", "Verifique anos, limites, timeout, tentativas e intervalo.")
            return

        if ano_ini > ano_fim:
            messagebox.showerror("Período inválido", "O ano inicial não pode ser maior que o ano final.")
            return
        if lim_string <= 0 or timeout <= 0 or tentativas <= 0 or delay_base < 0:
            messagebox.showerror("Valores inválidos", "Limites, timeout e tentativas devem ser maiores que zero.")
            return

        delays_custom = dict(DEFAULT_DELAYS)
        for p in plataformas:
            if p != "arXiv":
                delays_custom[p] = delay_base

        idiomas = [x.strip().lower() for x in self.ent_idiomas.get().split(",") if x.strip()]
        termos_incl = [x.strip() for x in self.ent_incl.get().split(",") if x.strip()]
        termos_excl = [x.strip() for x in self.ent_excl.get().split(",") if x.strip()]
        email = self.ent_email.get().strip() or "pesquisador@exemplo.com"
        api_key = self.ent_api_key.get().strip()
        pasta = self.ent_pasta.get().strip() or os.path.abspath("coleta_pesquisanator_xp")

        self._tempo_acumulado = 0.0
        self._inicio_execucao = None
        self._ultimo_xlsx = ""
        self.barra_geral.set_valor(0.0)
        self.barra_dl.set_valor(0.0)
        self.var_brutos.set("Registros: 0")
        self.var_pdfs.set("PDFs OK: 0")
        self.var_erros.set("Erros: 0")
        for b_p, l_p in self._barras_plat.values():
            b_p.set_valor(0.0)
            l_p.configure(text="Aguardando")

        cfg = ConfiguracaoExecucao(
            queries=queries,
            plataformas=plataformas,
            ano_ini=ano_ini,
            ano_fim=ano_fim,
            idiomas=idiomas,
            limite_por_string=lim_string,
            limite_total=lim_total,
            timeout=timeout,
            max_tentativas=tentativas,
            email=email,
            openalex_api_key=api_key,
            apenas_periodicos=self.var_periodicos.get(),
            apenas_oa=self.var_oa.get(),
            baixar_pdfs=self.var_baixar_pdf.get(),
            retomar_checkpoint=self.var_retomar.get(),
            termos_incl=termos_incl,
            termos_excl=termos_excl,
            pasta_saida=pasta,
            delays=delays_custom,
        )
        self.orquestrador.iniciar(cfg)

    def inserir_exemplo(self) -> None:
        self.txt_queries.delete("1.0", tk.END)
        self.txt_queries.insert(
            "1.0",
            'public budget transparency AND municipalities\n'
            'gestao orcamentaria setor publico\n'
            '"systematic literature review" AND "artificial intelligence"\n',
        )

    def limpar_campos(self) -> None:
        self.txt_queries.delete("1.0", tk.END)
        self.ent_incl.delete(0, tk.END)
        self.ent_excl.delete(0, tk.END)

    def selecionar_pasta(self) -> None:
        escolhida = filedialog.askdirectory(initialdir=self.ent_pasta.get() or os.getcwd())
        if escolhida:
            self.ent_pasta.delete(0, tk.END)
            self.ent_pasta.insert(0, escolhida)

    def abrir_pasta_saida(self) -> None:
        pasta = self.ent_pasta.get().strip() or os.path.abspath("coleta_pesquisanator_xp")
        os.makedirs(pasta, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(pasta)
            else:
                subprocess.Popen(["xdg-open", pasta])
        except Exception as exc:
            messagebox.showerror("Erro ao abrir pasta", f"Não foi possível abrir '{pasta}': {exc}")

    def abrir_xlsx(self) -> None:
        pasta = self.ent_pasta.get().strip() or os.path.abspath("coleta_pesquisanator_xp")
        caminho = self._ultimo_xlsx or os.path.join(pasta, "relatorio_coleta.xlsx")
        if not os.path.exists(caminho):
            messagebox.showinfo("Relatório XLSX", f"Arquivo não encontrado ainda:\n{caminho}")
            return
        try:
            if os.name == "nt":
                os.startfile(caminho)
            else:
                subprocess.Popen(["xdg-open", caminho])
        except Exception as exc:
            messagebox.showerror("Erro ao abrir XLSX", f"Não foi possível abrir '{caminho}': {exc}")

    def janela_ajuda_xp(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Manual Rápido — Pesquisanator XP")
        win.geometry("580x420")
        win.configure(bg=self.xp_bg)
        win.resizable(False, False)

        hdr = tk.Frame(win, bg=self.xp_blue_left, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  Manual de Operação (Determinístico, Sem IA)", bg=self.xp_blue_left, fg="#FFFFFF", font=self.f_bold).pack(side="left", padx=6)

        texto = (
            "1. BUSCA E PAGINAÇÃO:\n"
            "   • Insira uma string por linha. Operadores AND, OR e NOT são preservados.\n"
            "   • A paginação avança até o limite por string (e limite total, se definido).\n\n"
            "2. RESILIÊNCIA, MÉTRICAS E RETOMADA:\n"
            "   • Trata HTTP 429, timeouts e falhas com backoff exponencial e jitter.\n"
            "   • Consulta H-Index e CiteScore via OpenAlex Sources com cache local.\n"
            "   • Salva checkpoint_coleta.json para retomar coletas interrompidas.\n\n"
            "3. DOWNLOAD DE PDFS:\n"
            "   • Baixa em streaming para arquivo .part, valida %PDF e tamanho,\n"
            "     calcula SHA-256 e salva como AUTOR (ANO) - TITULO.pdf.\n\n"
            "4. RELATÓRIOS GERADOS:\n"
            "   • relatorio_coleta.xlsx, CSV, BibTeX e TXT com revista e métricas."
        )
        tk.Label(
            win,
            text=texto,
            bg=self.xp_panel,
            fg=self.xp_text,
            font=self.f_ui,
            justify="left",
            anchor="nw",
            relief="sunken",
            bd=2,
            padx=12,
            pady=10,
        ).pack(fill="both", expand=True, padx=10, pady=10)

        tk.Button(win, text="OK", width=12, bg=self.xp_btn_face, font=self.f_bold, relief="raised", bd=2, command=win.destroy).pack(pady=(0, 10))

    def janela_autoria_xp(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Sobre — Pesquisanator XP")
        win.geometry("500x310")
        win.configure(bg=self.xp_bg)
        win.resizable(False, False)

        hdr = tk.Frame(win, bg=self.xp_blue_left, height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  Sobre o Sistema e Autoria", bg=self.xp_blue_left, fg="#FFFFFF", font=self.f_bold).pack(side="left", padx=6)

        info = (
            f"{self.app_name} — {self.app_version}\n\n"
            f"Autoria: {self.autores}\n"
            f"Contato: {self.contato}\n\n"
            "Coleta bibliográfica determinística multi-base (OpenAlex, CrossRef,\n"
            "Europe PMC, DOAJ e arXiv) com deduplicação por proveniência,\n"
            "métricas de periódicos, download validado e relatório completo."
        )
        box = tk.Frame(win, bg=self.xp_panel, relief="sunken", bd=2)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Label(box, text=info, bg=self.xp_panel, fg=self.xp_text, font=self.f_ui, justify="left", anchor="nw", padx=12, pady=10).pack(fill="both", expand=True)

        tk.Button(win, text="Fechar", width=12, bg=self.xp_btn_face, font=self.f_bold, relief="raised", bd=2, command=win.destroy).pack(pady=(0, 10))

    def ao_fechar(self) -> None:
        if self._executando:
            self.orquestrador.cancelar()
        self.root.destroy()