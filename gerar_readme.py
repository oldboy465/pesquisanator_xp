from __future__ import annotations

from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def gerar_readme() -> Path:
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    conteudo = f"""# Pesquisanator XP

Coletor bibliográfico determinístico multi-base em Python com interface clássica estilo Windows XP (`tkinter`), deduplicação por proveniência, enriquecimento de métricas de periódicos (**H-Index**, **Quartil**, **CiteScore** e **SJR**), download seguro de PDFs com validação de integridade e exportação analítica completa (**XLSX**, **CSV**, **BibTeX** e **TXT**).

---

## Sumário

- [Visão Geral](#visão-geral)
- [Arquitetura do Projeto](#arquitetura-do-projeto)
- [Métricas de Periódicos e Impacto](#métricas-de-periódicos-e-impacto)
- [Instalação e Execução](#instalação-e-execução)
- [Relatórios e Artefatos Gerados](#relatórios-e-artefatos-gerados)
- [Autoria](#autoria)

---

## Visão Geral

O **Pesquisanator XP** foi projetado para apoiar revisões sistemáticas da literatura e levantamentos bibliográficos com reprodutibilidade e controle operacional:

- **Consulta multi-base**: `OpenAlex`, `CrossRef`, `Europe PMC`, `DOAJ` e `arXiv`.
- **Resiliência de rede**: controle de *rate limit* (`HTTP 429` com leitura de `Retry-After`), *backoff* exponencial com *jitter*, pausa/retomada/cancelamento e *checkpoint* em JSON (`checkpoint_coleta.json`).
- **Deduplicação e triagem determinística**: consolidação por DOI normalizado, identificador externo, título exato e similaridade textual (`SequenceMatcher`), seguida de filtros locais de inclusão/exclusão sobre título e resumo.
- **Download seguro de PDFs**: escrita em arquivo temporário `.part`, validação de assinatura `%PDF` e tamanho mínimo, cálculo de `SHA-256`, prevenção de duplicatas por hash/DOI e nomenclatura padronizada `AUTOR (ANO) - TITULO.pdf`.

---

## Arquitetura do Projeto

| Arquivo | Responsabilidade |
| :--- | :--- |
| `main.py` | Ponto de entrada, configuração de execução (`ConfiguracaoExecucao`) e orquestrador (`OrquestradorColeta`) em thread dedicada. |
| `interface.py` | Interface gráfica estilo Windows XP (`WindowsXPCollectorGUI` e `BarraProgressoXP`), console de execução, métricas ao vivo e controles de fluxo. |
| `conectores.py` | Sessão HTTP, requisição resiliente e conectores para OpenAlex, CrossRef, Europe PMC, DOAJ e arXiv. |
| `processamento.py` | Controle de execução (pausar/retomar/cancelar), checkpoint atômico, deduplicação com proveniência, triagem e download validado de PDFs. |
| `metricas_revista.py` | Consulta via `OpenAlex Sources` (`/sources`) por ISSN ou nome do periódico, cálculo de quartil (`Q1`–`Q4`) e cache local (`cache_metricas_revistas.json`). |
| `relatorios.py` | Exportação de `resultados_coleta.csv`, `referencias_coleta.bib`, `relatorio_coleta.txt` e `relatorio_coleta.xlsx` (com dashboard, gráficos nativos e aba `POR_REVISTAS`). |

---

## Métricas de Periódicos e Impacto

Como o nome do periódico não vai embutido no nome do arquivo PDF baixado e o SCImago não oferece API pública oficial direta, o módulo `metricas_revista.py` resolve os dados de cada revista e os consolida nos relatórios finais:

- **H-Index** e **CiteScore** (`2yr_mean_citedness`): recuperados via API da **OpenAlex Sources** com cache persistente em `cache_metricas_revistas.json`.
- **Quartil (`Q1`–`Q4`)**: classificado a partir do `h_index` resolvido quando disponível.
- **SJR**: registrado explicitamente como `N/D (Scimago sem API)` quando não há fonte automatizada disponível, preservando transparência metodológica sem inventar valores.

---

## Instalação e Execução

1. Instale as dependências:

```bash
pip install requests openpyxl