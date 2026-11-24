# Pesquisanator XP

Coletor bibliografico deterministico multi-base em Python com interface classica estilo Windows XP (`tkinter`), deduplicacao por proveniencia, enriquecimento de metricas de periodicos (**H-Index**, **Quartil**, **CiteScore** e **SJR**), download seguro de PDFs com validacao de integridade e exportacao analitica completa (**XLSX**, **CSV**, **BibTeX** e **TXT**).

---

## Sumario

- [Visao Geral](#visao-geral)
- [Arquitetura do Projeto](#arquitetura-do-projeto)
- [Metricas de Periodicos e Impacto](#metricas-de-periodicos-e-impacto)
- [Instalacao e Execucao](#instalacao-e-execucao)
- [Relatorios e Artefatos Gerados](#relatorios-e-artefatos-gerados)
- [Autoria](#autoria)

---

## Visao Geral

O **Pesquisanator XP** foi projetado para apoiar revisoes sistematicas da literatura e levantamentos bibliograficos com reprodutibilidade e controle operacional:

- **Consulta multi-base**: `OpenAlex`, `CrossRef`, `Europe PMC`, `DOAJ` e `arXiv`.
- **Resiliencia de rede**: controle de *rate limit* (`HTTP 429` com leitura de `Retry-After`), *backoff* exponencial com *jitter*, pausa/retomada/cancelamento e *checkpoint* em JSON (`checkpoint_coleta.json`).
- **Deduplicacao e triagem deterministica**: consolidacao por DOI normalizado, identificador externo, titulo exato e similaridade textual (`SequenceMatcher`), seguida de filtros locais de inclusao/exclusao sobre titulo e resumo.
- **Download seguro de PDFs**: escrita em arquivo temporario `.part`, validacao de assinatura `%PDF` e tamanho minimo, calculo de `SHA-256`, prevencao de duplicatas por hash/DOI e nomenclatura padronizada `AUTOR (ANO) - TITULO.pdf`.

---

## Arquitetura do Projeto

| Arquivo | Responsabilidade |
| :--- | :--- |
| `main.py` | Ponto de entrada, configuracao de execucao (`ConfiguracaoExecucao`) e orquestrador (`OrquestradorColeta`) em thread dedicada. |
| `interface.py` | Interface grafica estilo Windows XP (`WindowsXPCollectorGUI` e `BarraProgressoXP`), console de execucao, metricas ao vivo e controles de fluxo. |
| `conectores.py` | Sessao HTTP, requisicao resiliente e conectores para OpenAlex, CrossRef, Europe PMC, DOAJ e arXiv. |
| `processamento.py` | Controle de execucao (pausar/retomar/cancelar), checkpoint atomico, deduplicacao com proveniencia, triagem e download validado de PDFs. |
| `metricas_revista.py` | Consulta via `OpenAlex Sources` (`/sources`) por ISSN ou nome do periodico, calculo de quartil (`Q1`-`Q4`) e cache local (`cache_metricas_revistas.json`). |
| `relatorios.py` | Exportacao de `resultados_coleta.csv`, `referencias_coleta.bib`, `relatorio_coleta.txt` e `relatorio_coleta.xlsx` (com dashboard, graficos nativos e aba `POR_REVISTAS`). |

---

## Metricas de Periodicos e Impacto

Como o nome do periodico nao vai embutido no nome do arquivo PDF baixado e o SCImago nao oferece API publica oficial direta, o modulo `metricas_revista.py` resolve os dados de cada revista e os consolida nos relatorios finais:

- **H-Index** e **CiteScore** (`2yr_mean_citedness`): recuperados via API da **OpenAlex Sources** com cache persistente em `cache_metricas_revistas.json`.
- **Quartil (`Q1`-`Q4`)**: classificado a partir do `h_index` resolvido quando disponivel.
- **SJR**: registrado explicitamente como `N/D (Scimago sem API)` quando nao ha fonte automatizada disponivel, preservando transparencia metodologica sem inventar valores.

---

## Instalacao e Execucao

1. Instale as dependencias:

```bash
pip install requests openpyxl
```

2. Execute a aplicacao:

```bash
python main.py
```

---

## Relatorios e Artefatos Gerados

Na pasta de saida configurada (padrao: `coleta_pesquisanator_xp/`), sao gerados:

- `relatorio_coleta.xlsx`: planilha multi-aba com `DASHBOARD`, graficos nativos, `TODOS_OS_REGISTROS`, `ARTIGOS_APROVADOS`, `ARTIGOS_EXCLUIDOS`, `DOWNLOADS`, `POR_STRINGS`, `POR_PLATAFORMAS`, `POR_IDIOMAS`, `POR_ANO`, `POR_REVISTAS` e `EXECUCAO_E_ERROS`.
- `relatorio_coleta.txt`: resumo executivo, desempenho por string/plataforma, artigos aprovados com revista e metricas, ranking de revistas por H-Index e artigos excluidos.
- `resultados_coleta.csv`: base tabular completa em UTF-8 (`utf-8-sig`) incluindo colunas de revista e metricas.
- `referencias_coleta.bib`: exportacao em formato BibTeX.
- `pdfs_baixados/`: arquivos PDF validados (`%PDF` + `SHA-256`).

---

## Autoria

- **Philipe Lima & Colaboradores**
- GitHub: [oldboy465](https://github.com/oldboy465) | Repositorio: [pesquisanator_xp](https://github.com/oldboy465/pesquisanator_xp)

_README gerado automaticamente em 23/11/2026 21:29 via `gerar_readme.py`._
