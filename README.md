<div align="center">

# 📡 tg-posts-scraper

**An extensible local framework for scraping Telegram channels and processing data through a scheduled multi-LLM agent pipeline with structured document output.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Telethon](https://img.shields.io/badge/Telethon-1.42-2CA5E0?logo=telegram)](https://github.com/LonamiWebs/Telethon)
[![Groq](https://img.shields.io/badge/Groq-LPU%20Inference-orange)](https://groq.com/)
[![Mistral](https://img.shields.io/badge/MistralAI-Stable%20Endpoint-blueviolet)](https://mistral.ai/)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-Multi--Model-green)](https://openrouter.ai/)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063)](https://docs.pydantic.dev/)
[![python-docx](https://img.shields.io/badge/python--docx-Document%20Output-2B579A)](https://python-docx.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 🧭 Overview

`tg-posts-scraper` is a **production-grade, locally-run intelligence pipeline** designed to automatically collect posts from public Telegram channels, process them through a multi-layer LLM agent pool, and produce structured analytical reports — all driven by declarative preset configurations.

The system is built around three core ideas:

1. **Zero-cost resilience** — a layered inference pool that chains together multiple free-tier LLM providers (Groq, Mistral, OpenRouter) with smart fallback logic, ensuring nearly 100% batch completion even under rate limits and quota exhaustion.
2. **Preset-driven operations** — every run is fully described by a human-readable configuration file (preset), making the tool reproducible, auditable, and trivially extensible to new use cases.
3. **Structured output** — raw posts and final AI-generated summaries are persisted in structured formats, with document-ready output via `python-docx`.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🔁 **Multi-LLM Inference Pool** | 7-layer fallback agent chain across Groq, Mistral, and OpenRouter |
| ⚡ **Async-first Architecture** | Fully asynchronous pipeline using `asyncio` from scraping to inference |
| 🗺️ **Region-aware Scraping** | Groups channels by geographic region for structured multi-source analysis |
| 📦 **Preset System** | Declarative YAML-based scenario configs — swap tasks without touching code |
| 🔒 **Secure Secrets Management** | Pydantic `SecretStr` + `.env` file — zero plaintext credentials in code |
| 📄 **Document Builder Output** | Generates polished `.docx` reports via `python-docx` |
| 💾 **Dual-save Strategy** | Raw posts saved immediately; AI summaries saved per-region as they complete |
| 🧩 **Pluggable Agent Interface** | Add any new LLM provider by implementing a single agent class |

---

## 🗂️ Project Structure

```
tg-posts-scraper/
├── main.py                  # Entry point & pipeline orchestration
│
├── agents/                  # LLM provider adapters
│   ├── groq_agent.py        # Groq LPU inference agent
│   ├── mistral_agent.py     # Mistral AI agent
│   └── openrouter_agent.py  # OpenRouter multi-model agent
│
├── modules/                 # Core framework modules
│   ├── pipeline.py          # PipelineManager — concurrent batch inference engine
│   ├── telegram_scraper.py  # TelegramScraper — async Telethon-based collector
│   ├── result_saver.py      # ResultSaver — dual-mode output persistence
│   └── preset_loader.py     # PresetLoader — scenario & config loader
│
├── configs/
│   └── settings.py          # Pydantic settings (env-based, SecretStr keys)
│
├── prompts/                 # Prompt templates (one per task type)
├── data/                    # Runtime output: raw posts + AI results
│   ├── raw/
│   └── results/
│
├── scripts/                 # Utility / maintenance scripts
├── requirements.txt
└── .gitignore
```

---

## 🏗️ Architecture Deep Dive

### 1. PresetLoader — Declarative Scenario Engine

Every run of the framework is governed by a **preset** — a structured configuration file that completely describes a scanning scenario. Loading a preset bootstraps the entire system:

```python
preset = PresetLoader.load('honor_guardian_scan_2026')
prompt  = PresetLoader.load_prompt(preset.prompt_file)
regions = PresetLoader.load_regions(preset.regions)
# → { "Регион A": ["@channel1", "@channel2"], "Регион B": [...] }
```

A preset encodes:
- **`prompt_file`** — which analytical prompt template to use
- **`regions`** — a mapping of region names to their Telegram channel lists
- **`scraper.stop_date`** — how far back to collect posts
- **`pipeline.max_concurrent_tasks`** — parallelism level for inference
- **`pipeline.max_chars_per_batch`** — token budget per inference batch

This design means **adding a new monitoring scenario requires zero code changes** — write a new preset YAML and run.

---

### 2. TelegramScraper — Async Regional Collector

The scraper wraps **Telethon** in an async context manager and implements region-aware collection:

```python
async with TelegramScraper(session_path=..., api_id=..., api_hash=...) as scraper:
    posts = await scraper.scrape_region(channels=channels, stop_date=preset.scraper.stop_date)
```

Key behaviors:
- Iterates over all channels within a region in a single async session
- Respects the `stop_date` boundary — only collects posts newer than the configured cutoff
- Returns `None`/empty safely if a channel has no new posts, allowing graceful skips
- Uses a persistent session file to avoid repeated Telegram authentication

---

### 3. PipelineManager — Multi-LLM Inference Engine

`PipelineManager` is the heart of the system. It takes a list of agent objects (ordered by priority), batches the raw post data to respect per-model context limits, and dispatches inference tasks concurrently with automatic fallback.

```python
pipeline = PipelineManager(
    agents=agents_list,
    max_concurrent_tasks=preset.pipeline.max_concurrent_tasks,
    max_chars_per_batch=preset.pipeline.max_chars_per_batch,
    max_retries=len(agents_list) + 1
)
summary = await pipeline.run(prompt=prompt, data=posts)
```

**How fallback works:** If an agent fails (rate limit, 404, quota exhaustion), the manager automatically promotes the next agent in the list. The `max_retries` ceiling equals the total number of configured agents plus a safety buffer — so every agent gets a chance before the batch is declared lost.

---

### 4. The 7-Layer Inference Pool

The agent pool is intentionally designed as a **layered defense** against API instability and rate limiting — an architecture that treats free-tier LLM endpoints as unreliable but collectively sufficient:

```
┌───────────────────────────────────────────────────────────┐
│  LAYER 1 — GRADERS (Maximum Speed)                        │
│  Groq Key 1 · llama-3.1-8b-instant · max_retries=1       │
│  "First responders. Near-instant throughput."             │
├───────────────────────────────────────────────────────────┤
│  LAYER 2 — DUPLICATORS (Quota Bypass)                     │
│  Groq Key 2 · llama-3.1-8b-instant · max_retries=1       │
│  "Second key rotates TPM limits."                         │
├───────────────────────────────────────────────────────────┤
│  LAYER 3 — STABLE RESERVE                                 │
│  Mistral · max_retries=2                                  │
│  "Reliable, free, rarely 404s."                           │
├───────────────────────────────────────────────────────────┤
│  LAYER 4-5 — QUALITY FREE (OpenRouter)           [OPT]    │
│  Gemini Flash, Qwen 2.5, StepFun Step-3.5, Qwen3         │
│  "Smarter models, possible queue delays."                 │
├───────────────────────────────────────────────────────────┤
│  LAYER 6 — HIGH-QUALITY GROQ (Both Keys)                 │
│  llama-4-scout-17b · gpt-oss-safeguard-20b               │
│  "Highest intelligence, low RPM quota — key rotation."   │
├───────────────────────────────────────────────────────────┤
│  LAYER 7 — GLOBAL FALLBACK (OpenRouter Wildcard) [OPT]   │
│  openrouter/free — any live endpoint                      │
│  "Last resort: batch must not be lost."                   │
└───────────────────────────────────────────────────────────┘
```

Optional layers (4, 5, 7) are pre-configured in code and can be enabled with a single uncomment — providing a playground of model quality vs. availability tradeoffs.

---

### 5. ResultSaver — Dual-mode Persistence

`ResultSaver` persists data at two distinct moments in the run lifecycle:

- **Immediately after scraping** → `saver.save_raw_posts(region_name, posts)` — raw channel data is written to disk before any AI processing begins. This ensures scraped data is never lost if inference fails.
- **Immediately after each region's inference** → `saver.save_result(region_name, summary)` — results are written region-by-region, not at the end. A failure midway through does not erase already-completed summaries.

The saver is preset-aware, organizing output into directories derived from the active scenario name.

---

### 6. Document Builder (python-docx)

The output layer uses `python-docx` to render AI-generated summaries into properly formatted Word documents. This enables:
- Automatic heading hierarchy derived from region structure
- Consistent typography and section styling across all reports
- Output suitable for direct use in briefings, reports, or archival

---

### 7. Secure Configuration (Pydantic Settings)

All credentials are managed through `pydantic-settings` with `SecretStr` fields, loaded from a `.env` file at runtime. No API keys are ever present in source code.

```python
mistral_key  = settings.api.mistral_key.get_secret_value()
groq_key_1   = settings.api.groq_main.get_secret_value()
groq_key_2   = settings.api.groq_reserve.get_secret_value()
```

---

## 🔄 Full Pipeline Flow

```
┌─────────────────────┐
│   Load Preset       │  ← Scenario name → channels, prompt, params
└────────┬────────────┘
         │
┌────────▼────────────┐
│  TelegramScraper    │  ← Telethon session → async per-region scrape
│  (per region)       │
└────────┬────────────┘
         │
┌────────▼────────────┐
│  save_raw_posts()   │  ← Immediately persists raw data to disk
└────────┬────────────┘
         │
┌────────▼────────────┐
│  PipelineManager    │  ← Batches posts, dispatches to agent pool
│  .run(prompt, data) │
└────────┬────────────┘
         │
   ┌─────▼──────────────────────────────────┐
   │  Agent Pool (7-layer fallback chain)   │
   │  Groq → Mistral → OpenRouter → ...     │
   └─────┬──────────────────────────────────┘
         │
┌────────▼────────────┐
│  save_result()      │  ← AI summary written per region as it completes
└────────┬────────────┘
         │
┌────────▼────────────┐
│  Document Builder   │  ← python-docx renders final .docx report
└─────────────────────┘
```

---

## ⚙️ Installation

**Prerequisites:** Python 3.11+, a Telegram API account ([my.telegram.org](https://my.telegram.org/))

```bash
# 1. Clone the repository
git clone https://github.com/AstrakhantsevM/tg-posts-scraper.git
cd tg-posts-scraper

# 2. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate        # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 🔑 Configuration

Create a `.env` file in the project root:

```env
# Telegram API credentials (from https://my.telegram.org)
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_SESSION_PATH=./data/session/my_session

# LLM API Keys
G1KEY=gsk_...
G2KEY=gsk_...
M1KEY=...
O1KEY=sk-or-...
```

---

## 🚀 Running the Framework

```bash
# Activate your virtual environment, then:
python main.py
```

The active preset is set inside `main.py`:
```python
preset = PresetLoader.load('honor_guardian_scan_2026')
```

Change the preset name to switch between monitoring scenarios. Output will appear in `data/raw/` and `data/results/`.

---

## 🧩 Extending the Framework

### Add a New LLM Provider

1. Create `agents/my_provider_agent.py` implementing the same async interface as existing agents.
2. Instantiate it in `main.py` and insert it at the desired layer position in `agents_list`.

### Add a New Scenario

1. Create a new preset YAML in `configs/presets/`.
2. Add a corresponding prompt file to `prompts/`.
3. Define your region → channel mapping.
4. Change the preset name in `main.py` and run.

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `Telethon` | Telegram MTProto client for async channel scraping |
| `groq` | Groq LPU inference API client |
| `mistralai` | Mistral AI API client |
| `openai` | OpenRouter-compatible OpenAI SDK interface |
| `pydantic` / `pydantic-settings` | Data validation & secure settings management |
| `python-docx` | Structured `.docx` document generation |
| `python-dotenv` | Environment variable loading |
| `lxml` | XML/HTML parsing utilities |
| `asyncio` | Core async runtime (stdlib) |

---

## 🛡️ Design Philosophy

> **"Build for failure at every layer."**

Free-tier LLM APIs are fast and capable but fundamentally unreliable under load. Rather than treating any single provider as authoritative, this framework treats the **pool of providers** as the reliable unit. Any individual agent can fail silently — the pipeline continues. Only when every configured layer is exhausted does a batch fail, and even then the system logs critically rather than silently dropping data.

This approach makes it practical to run large-scale analytical workloads at **zero infrastructure cost**, using only free API tiers across multiple providers.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">

Built with Python · Powered by Telethon, Groq, Mistral & OpenRouter

</div>
