"""FastAPI 後端

啟動：
  uv run uvicorn api.main:app --reload --port 8000

提供：
  GET    /api/health
  GET    /api/strategies              列出所有策略
  GET    /api/strategies/defaults     回傳預設參數 schema
  GET    /api/strategies/{id}         取單一策略
  POST   /api/strategies              新增 / 更新策略
  DELETE /api/strategies/{id}         刪除
  POST   /api/strategies/generate     AI 生策略 (Gemini)
  GET    /api/market                  目前大盤狀態
  GET    /api/watchlist               讀 watchlist
  POST   /api/run                     用指定策略跑一次完整評分
  GET    /api/run/latest              取得最後一次執行結果
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from stock_strategies import loader
from stock_strategies.evaluate import evaluate
from stock_strategies.market import apply_market_filter, get_market_state
from stock_strategies.sheet import read_watchlist

from api.services.ai_generator import generate_strategy_with_ai


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="Stock Strategies API",
    version="1.0.0",
)


# ============================================================
# 本機資料儲存
# ============================================================

# 專案根目錄
BASE_DIR = Path(__file__).resolve().parent.parent

# F:\Codex\stock-strategies-only\data
DATA_DIR = BASE_DIR / "data"

# F:\Codex\stock-strategies-only\data\last_run.json
LAST_RUN_FILE = DATA_DIR / "last_run.json"


def _json_default(obj):
    """
    處理 numpy 等非 Python 原生型別。

    例如：
      numpy.bool_
      numpy.int64
      numpy.float64
    """
    if hasattr(obj, "item"):
        return obj.item()

    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


def save_last_run(data: dict):
    """把最後一次選股結果存到本機 JSON。"""

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        **data,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    LAST_RUN_FILE.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ),
        encoding="utf-8",
    )


# ============================================================
# CORS
# ============================================================

# dev 期間給 localhost:3000 (Next.js)
_origins_env = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in _origins_env.split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Schemas
# ============================================================

class StrategyIn(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = ""
    source: Optional[str] = "manual"
    params: dict[str, Any] = Field(default_factory=dict)


class AIGenerateIn(BaseModel):
    prompt: str = Field(
        ...,
        description="使用者用自然語言描述想要的策略風格",
    )
    name: Optional[str] = None


class RunIn(BaseModel):
    strategy_id: str
    limit: Optional[int] = Field(
        None,
        description="只跑前 N 檔（debug 用）",
    )


# ============================================================
# Routes
# ============================================================

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ts": int(time.time()),
    }


# ------------------------------------------------------------
# 最後一次執行結果
# ------------------------------------------------------------

@app.get("/api/run/latest")
def latest_run():
    """
    讀取本機最後一次執行結果。
    如果尚未執行過，回傳 result = null。
    """

    if not LAST_RUN_FILE.exists():
        return {
            "result": None,
        }

    try:
        result = json.loads(
            LAST_RUN_FILE.read_text(
                encoding="utf-8"
            )
        )

        return {
            "result": result,
        }

    except Exception as e:
        raise HTTPException(
            500,
            f"讀取最後一次執行結果失敗：{e}",
        )


# ------------------------------------------------------------
# Strategies
# ------------------------------------------------------------

@app.get("/api/strategies")
def list_strategies():
    return {
        "strategies": loader.list_strategies()
    }


@app.get("/api/strategies/defaults")
def defaults():
    return {
        "params": loader.param_defaults()
    }


@app.get("/api/strategies/{sid}")
def get_strategy(sid: str):
    s = loader.get_strategy(sid)

    if not s:
        raise HTTPException(
            404,
            f"找不到策略 {sid}",
        )

    return s


@app.post("/api/strategies")
def save_strategy(payload: StrategyIn):
    try:
        clean = loader.save_strategy(
            payload.model_dump()
        )

        return clean

    except loader.StrategyError as e:
        raise HTTPException(
            400,
            str(e),
        )


@app.delete("/api/strategies/{sid}")
def delete_strategy(sid: str):

    if sid in (
        "default",
        "conservative",
    ):
        raise HTTPException(
            400,
            "預設策略不可刪除",
        )

    ok = loader.delete_strategy(sid)

    if not ok:
        raise HTTPException(
            404,
            f"找不到策略 {sid}",
        )

    return {
        "ok": True,
    }


# ------------------------------------------------------------
# AI Strategy
# ------------------------------------------------------------

@app.post("/api/strategies/generate")
def generate_strategy(payload: AIGenerateIn):

    try:
        strategy = generate_strategy_with_ai(
            payload.prompt,
            name=payload.name,
        )

        return strategy

    except Exception as e:

        traceback.print_exc()

        raise HTTPException(
            500,
            f"AI 生策略失敗：{e}",
        )


# ------------------------------------------------------------
# Market
# ------------------------------------------------------------

@app.get("/api/market")
def market():

    return get_market_state()


# ------------------------------------------------------------
# Watchlist
# ------------------------------------------------------------

@app.get("/api/watchlist")
def watchlist():

    try:
        return {
            "items": read_watchlist()
        }

    except Exception as e:

        # 沒設定 Google Sheet 時
        # 不要整個 API 500
        return {
            "items": [],
            "error": str(e),
        }


# ------------------------------------------------------------
# Run Strategy
# ------------------------------------------------------------

@app.post("/api/run")
def run(payload: RunIn):

    # --------------------------------------------------------
    # 1. 取得策略
    # --------------------------------------------------------

    strategy = loader.get_strategy(
        payload.strategy_id
    )

    if not strategy:
        raise HTTPException(
            404,
            f"找不到策略 {payload.strategy_id}",
        )


    # --------------------------------------------------------
    # 2. 讀取 Watchlist
    # --------------------------------------------------------

    try:
        wl = read_watchlist()

    except Exception as e:

        raise HTTPException(
            500,
            f"讀取 watchlist 失敗：{e}",
        )


    # Debug 模式只跑前 N 檔
    if payload.limit:
        wl = wl[: payload.limit]


    # --------------------------------------------------------
    # 3. 策略參數
    # --------------------------------------------------------

    params = strategy["params"]

    market_filter_on = params.get(
        "market_filter_enabled",
        True,
    )


    # --------------------------------------------------------
    # 4. 大盤狀態
    # --------------------------------------------------------

    if market_filter_on:

        market_state = get_market_state(
            int(
                params.get(
                    "market_filter_ma_period",
                    20,
                )
            )
        )

    else:

        market_state = {
            "bullish": True,
            "note": "已關閉大盤濾鏡",
        }


    # --------------------------------------------------------
    # 5. 個股評分
    # --------------------------------------------------------

    results = []

    for row in wl:

        sid = str(
            row["stock_id"]
        )

        name = row.get(
            "name",
            "",
        )

        r = evaluate(
            sid,
            name,
            strategy=strategy,
        )

        if r:
            results.append(r)

        # 避免 API 呼叫過快
        time.sleep(0.4)


    # --------------------------------------------------------
    # 6. 大盤濾鏡
    # --------------------------------------------------------

    if market_filter_on:

        downgraded = apply_market_filter(
            results,
            market_state,
        )

    else:

        downgraded = 0


    # --------------------------------------------------------
    # 7. 排序
    # --------------------------------------------------------

    order = {
        "BUY": 0,
        "WATCH": 1,
        "SKIP": 2,
        "ERROR": 3,
    }

    results.sort(
        key=lambda x: (
            order.get(
                x.get("action"),
                4,
            ),
            -x.get(
                "signal_score",
                0,
            ),
        )
    )


    # --------------------------------------------------------
    # 8. 組合 API Response
    # --------------------------------------------------------

    response = {

        "strategy": {
            "id": strategy["id"],
            "name": strategy["name"],
        },

        "market": market_state,

        "downgraded": downgraded,

        "summary": {

            "total": len(results),

            "buy": sum(
                1
                for r in results
                if r.get("action") == "BUY"
            ),

            "watch": sum(
                1
                for r in results
                if r.get("action") == "WATCH"
            ),

            "skip": sum(
                1
                for r in results
                if r.get("action") == "SKIP"
            ),

            "error": sum(
                1
                for r in results
                if r.get("action") == "ERROR"
            ),
        },

        "results": results,
    }


    # --------------------------------------------------------
    # 9. 儲存最後一次結果
    # --------------------------------------------------------

    try:

        save_last_run(
            response
        )

    except Exception as e:

        # JSON 儲存失敗不能影響選股本身
        print(
            f"⚠️ last_run.json 儲存失敗：{e}"
        )


    # --------------------------------------------------------
    # 10. 回傳前端
    # --------------------------------------------------------

    return response