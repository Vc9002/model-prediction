"""Local Models MCP Server.

Exposes local machine models via the Model Context Protocol (stdio transport):
1. Local LLM models (Ollama / Qwen 2.5 Coder):
   - ollama_generate: Prompt local LLM (default: qwen2.5-coder:7b)
   - ollama_list_models: List installed local models
2. Local Quantitative Sports Prediction Models:
   - predict_nba_structural: NBA Structural v5 game forecast
   - predict_mlb_runline: MLB Structural Runline v4 (-1.5 / +1.5)
   - predict_mlb_residual: MLB Moneyline Market Residual v10
   - predict_wnba_structural: WNBA Structural v3 game forecast
   - predict_esports_contextual: Esports Contextual v7 (CS2, LoL, Dota2, Valorant, R6)
   - predict_international_baseball: KBO & NPB tie-aware structural forecast
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# Ensure project root is on sys.path
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

OLLAMA_BASE_URL = "http://127.0.0.1:11434"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ollama_generate",
        "description": "Generate text using local Ollama LLM (e.g., qwen2.5-coder:7b) running on RTX 5060 GPU.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The input prompt or instruction for the local model.",
                },
                "model": {
                    "type": "string",
                    "description": "Model tag to use.",
                    "default": "qwen2.5-coder:7b",
                },
                "system": {"type": "string", "description": "Optional system prompt."},
                "temperature": {"type": "number", "description": "Sampling temperature.", "default": 0.7},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "ollama_list_models",
        "description": "List all installed local models available in Ollama on this machine.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "predict_nba_structural",
        "description": "Run the NBA Structural v5 score simulation and market pricing engine.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "home_team": {"type": "string", "description": "Home team name or abbreviation."},
                "away_team": {"type": "string", "description": "Away team name or abbreviation."},
                "home_pace": {"type": "number", "description": "Home pace (default: 99.2).", "default": 99.2},
                "away_pace": {"type": "number", "description": "Away pace (default: 99.2).", "default": 99.2},
                "home_ortg": {
                    "type": "number",
                    "description": "Home offensive rating (default: 114.5).",
                    "default": 114.5,
                },
                "home_drtg": {
                    "type": "number",
                    "description": "Home defensive rating (default: 114.5).",
                    "default": 114.5,
                },
                "away_ortg": {
                    "type": "number",
                    "description": "Away offensive rating (default: 114.5).",
                    "default": 114.5,
                },
                "away_drtg": {
                    "type": "number",
                    "description": "Away defensive rating (default: 114.5).",
                    "default": 114.5,
                },
                "home_rest_days": {
                    "type": "integer",
                    "description": "Home rest days (0 = back-to-back).",
                    "default": 2,
                },
                "away_rest_days": {
                    "type": "integer",
                    "description": "Away rest days (0 = back-to-back).",
                    "default": 2,
                },
                "spread_home_line": {
                    "type": "number",
                    "description": "Spread line for home team (e.g. -5.5).",
                    "default": -5.5,
                },
                "total_line": {
                    "type": "number",
                    "description": "Over/under total line (e.g. 228.5).",
                    "default": 228.5,
                },
            },
            "required": ["home_team", "away_team"],
        },
    },
    {
        "name": "predict_mlb_runline",
        "description": "Derive structural MLB runline (-1.5 / +1.5) cover probabilities from joint Poisson runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "home_team": {"type": "string", "description": "Home team name or code."},
                "away_team": {"type": "string", "description": "Away team name or code."},
                "lambda_home": {"type": "number", "description": "Expected runs for home team."},
                "lambda_away": {"type": "number", "description": "Expected runs for away team."},
            },
            "required": ["home_team", "away_team", "lambda_home", "lambda_away"],
        },
    },
    {
        "name": "predict_mlb_residual",
        "description": "Forecast MLB moneyline win probabilities combining devigged market prior and residual features.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market_fair_prob_home": {
                    "type": "number",
                    "description": "Devigged market implied probability for home team (0.0 to 1.0).",
                },
                "market_open_prob_home": {
                    "type": "number",
                    "description": "Opening market probability for home team.",
                },
                "lineup_woba_delta_home": {
                    "type": "number",
                    "description": "Lineup wOBA delta for home team.",
                    "default": 0.0,
                },
                "starter_csw_delta": {
                    "type": "number",
                    "description": "Starting pitcher CSW% delta.",
                    "default": 0.0,
                },
                "starter_xwoba_allowed_delta": {
                    "type": "number",
                    "description": "Starter xwOBA allowed delta.",
                    "default": 0.0,
                },
            },
            "required": ["market_fair_prob_home", "market_open_prob_home"],
        },
    },
    {
        "name": "predict_wnba_structural",
        "description": "Unified WNBA structural simulation engine for Moneyline, Spread, and Totals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "home_team": {"type": "string", "description": "Home team."},
                "away_team": {"type": "string", "description": "Away team."},
                "home_pace": {"type": "number", "description": "Home pace.", "default": 80.5},
                "away_pace": {"type": "number", "description": "Away pace.", "default": 80.5},
                "home_rest_days": {"type": "integer", "description": "Home rest days.", "default": 2},
                "away_rest_days": {"type": "integer", "description": "Away rest days.", "default": 2},
                "spread_home_line": {
                    "type": "number",
                    "description": "Home spread line (e.g. -4.5).",
                    "default": -4.5,
                },
                "total_line": {"type": "number", "description": "Total line (e.g. 165.5).", "default": 165.5},
            },
            "required": ["home_team", "away_team"],
        },
    },
    {
        "name": "predict_esports_contextual",
        "description": "Contextual residual match prediction over Tiered Elo for CS2, Dota2, LoL, Valorant, or Rainbow Six.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_title": {
                    "type": "string",
                    "description": "cs2, dota2, lol, valorant, or rainbow_six.",
                    "enum": ["cs2", "dota2", "lol", "valorant", "rainbow_six"],
                },
                "team_a": {"type": "string", "description": "Team A name."},
                "team_b": {"type": "string", "description": "Team B name."},
                "elo_prob_a": {
                    "type": "number",
                    "description": "Tiered Elo win probability for Team A (0.0 to 1.0).",
                },
                "map_name": {"type": "string", "description": "Optional map name."},
                "is_lan": {
                    "type": "boolean",
                    "description": "True if LAN event, False if Online.",
                    "default": True,
                },
                "series_format": {"type": "string", "description": "Bo1, Bo3, or Bo5.", "default": "Bo3"},
                "side_a": {
                    "type": "string",
                    "description": "Starting side for Team A (blue/red, ct/t, radiant/dire).",
                    "default": "blue",
                },
            },
            "required": ["game_title", "team_a", "team_b", "elo_prob_a"],
        },
    },
    {
        "name": "predict_international_baseball",
        "description": "Tie-aware structural baseball forecast for KBO (Korea) and NPB (Japan).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league": {"type": "string", "description": "KBO or NPB.", "enum": ["KBO", "NPB"]},
                "home_team": {"type": "string", "description": "Home team."},
                "away_team": {"type": "string", "description": "Away team."},
                "lambda_home": {
                    "type": "number",
                    "description": "Expected runs for home team.",
                    "default": 4.2,
                },
                "lambda_away": {
                    "type": "number",
                    "description": "Expected runs for away team.",
                    "default": 3.8,
                },
                "total_line": {"type": "number", "description": "Over/under total line.", "default": 8.5},
            },
            "required": ["league", "home_team", "away_team"],
        },
    },
]


def _call_ollama(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{OLLAMA_BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama connection error on {url}: {exc}. Is 'ollama serve' running?") from exc


def handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "ollama_generate":
        prompt = arguments["prompt"]
        model = arguments.get("model", "qwen2.5-coder:7b")
        system = arguments.get("system")
        temperature = arguments.get("temperature", 0.7)
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        res = _call_ollama("/api/generate", payload)
        return {
            "response": res.get("response", ""),
            "model": res.get("model", model),
            "done": res.get("done", True),
        }

    elif name == "ollama_list_models":
        res = _call_ollama("/api/tags")
        models = [
            {
                "name": m.get("name"),
                "size_gb": round(m.get("size", 0) / (1024**3), 2),
                "modified_at": m.get("modified_at"),
            }
            for m in res.get("models", [])
        ]
        return {"models": models, "count": len(models)}

    elif name == "predict_nba_structural":
        from model_prediction.models.nba_structural_v5 import NBAStructuralV5Engine

        engine = NBAStructuralV5Engine()
        fc = engine.forecast_game(
            home_team=arguments["home_team"],
            away_team=arguments["away_team"],
            home_pace=arguments.get("home_pace", 99.2),
            away_pace=arguments.get("away_pace", 99.2),
            home_ortg=arguments.get("home_ortg", 114.5),
            home_drtg=arguments.get("home_drtg", 114.5),
            away_ortg=arguments.get("away_ortg", 114.5),
            away_drtg=arguments.get("away_drtg", 114.5),
            home_rest_days=arguments.get("home_rest_days", 2),
            away_rest_days=arguments.get("away_rest_days", 2),
            spread_home_line=arguments.get("spread_home_line", -5.5),
            total_line=arguments.get("total_line", 228.5),
        )
        return fc.__dict__

    elif name == "predict_mlb_runline":
        from model_prediction.models.mlb_runline_v4 import MLBStructuralRunlineV4Model

        model = MLBStructuralRunlineV4Model()
        fc = model.forecast_runline(
            home_team=arguments["home_team"],
            away_team=arguments["away_team"],
            lambda_home=arguments["lambda_home"],
            lambda_away=arguments["lambda_away"],
        )
        data = dict(fc.__dict__)
        data.pop("bivariate_run_matrix", None)  # Omit large matrix from summary
        return data

    elif name == "predict_mlb_residual":
        from model_prediction.models.mlb_market_residual_v10 import (
            MLBMarketResidualV10Model,
            MLBResidualFeatures,
        )

        features = MLBResidualFeatures(
            market_fair_prob_home=arguments["market_fair_prob_home"],
            market_open_prob_home=arguments["market_open_prob_home"],
            lineup_woba_delta_home=arguments.get("lineup_woba_delta_home", 0.0),
            starter_csw_delta=arguments.get("starter_csw_delta", 0.0),
            starter_xwoba_allowed_delta=arguments.get("starter_xwoba_allowed_delta", 0.0),
        )
        model = MLBMarketResidualV10Model()
        fc = model.forecast_matchup(features)
        return fc.__dict__

    elif name == "predict_wnba_structural":
        from model_prediction.models.wnba_structural_v3 import WNBAStructuralV3Engine

        engine = WNBAStructuralV3Engine()
        fc = engine.forecast_game(
            home_team=arguments["home_team"],
            away_team=arguments["away_team"],
            home_pace=arguments.get("home_pace", 80.5),
            away_pace=arguments.get("away_pace", 80.5),
            home_rest_days=arguments.get("home_rest_days", 2),
            away_rest_days=arguments.get("away_rest_days", 2),
            spread_home_line=arguments.get("spread_home_line", -4.5),
            total_line=arguments.get("total_line", 165.5),
        )
        return fc.__dict__

    elif name == "predict_esports_contextual":
        from model_prediction.models.esports_contextual_v7 import (
            EsportsContextFeatures,
            EsportsContextualV7Model,
        )

        features = EsportsContextFeatures(
            game_title=arguments["game_title"],
            team_a=arguments["team_a"],
            team_b=arguments["team_b"],
            elo_prob_a=arguments["elo_prob_a"],
            map_name=arguments.get("map_name"),
            is_lan=arguments.get("is_lan", True),
            series_format=arguments.get("series_format", "Bo3"),
            side_a=arguments.get("side_a", "blue"),
        )
        model = EsportsContextualV7Model()
        fc = model.forecast_match(features)
        return fc.__dict__

    elif name == "predict_international_baseball":
        from model_prediction.models.international_baseball_v3 import InternationalBaseballV3Model

        model = InternationalBaseballV3Model(league=arguments["league"])
        fc = model.forecast_match(
            home_team=arguments["home_team"],
            away_team=arguments["away_team"],
            lambda_home=arguments.get("lambda_home", 4.2),
            lambda_away=arguments.get("lambda_away", 3.8),
            total_line=arguments.get("total_line", 8.5),
        )
        data = dict(fc.__dict__)
        data.pop("bivariate_score_matrix", None)
        return data

    else:
        raise ValueError(f"Unknown tool: {name}")


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "local-models",
                        "version": "1.0.0",
                    },
                },
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            # Client acknowledgement, no response required
            pass

        elif method == "ping":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}) + "\n")
            sys.stdout.flush()

        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                res = handle_tool_call(tool_name, tool_args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res, indent=2),
                            }
                        ]
                    },
                }
            except Exception as exc:  # noqa: BLE001
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing {tool_name}: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        else:
            # Method not found
            if msg_id is not None:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method {method!r} not found"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
