# CLI Token Usage

The model-prediction CLI (`python -m model_prediction.cli`) is **entirely local** —
it never calls Codewhale, ChatGPT, Claude, or any other LLM API. Every subcommand
runs as a local Python process on your machine.

## What the CLI actually does

| Layer | How | Calls what |
|---|---|---|
| Models | In-process Python | `numpy`, `scipy`, `scikit-learn` |
| Data | HTTP requests | ESPN public API, Polymarket gateway, bo3.gg, MLB StatsAPI |
| Storage | Local filesystem | `.xlsx`, `.jsonl`, `.json` |
| Dashboard | Local HTTP server | `localhost:8765` |

## What the CLI does NOT do

- Does NOT call any LLM API (Codex, OpenAI, Anthropic, etc.)
- Does NOT use Codewhale tokens or credits
- Does NOT require an internet connection beyond the free sports data APIs
- Does NOT have any AI/ML-as-a-service dependency

## Cost profile

- **Codewhale tokens**: zero — the CLI is a standalone Python program
- **API costs**: zero — all data sources are free/public (ESPN, Polymarket gateway, MLB StatsAPI)
  - The Odds API requires a free API key (`THE_ODDS_API_KEY`) but no payment
  - SportsDataIO is optional and unused in the default pipeline
- **Compute**: runs on your machine's CPU — no cloud compute costs

## When Codewhale IS used

Codewhale tokens are only consumed when you ask Codewhale to:
- Read/analyze project files
- Write/modify code
- Explain or debug issues
- Generate documentation

Running the CLI yourself (`cli daily`, `cli settle`, etc.) from a terminal
consumes zero Codewhale tokens regardless of how many forecasts you generate.
