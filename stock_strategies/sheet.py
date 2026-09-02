import os
import json

import gspread
from google.oauth2.service_account import Credentials


def get_gsheet():
    creds_json = os.environ["GOOGLE_CREDS_JSON"]
    creds_dict = json.loads(creds_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=scopes,
    )

    gc = gspread.authorize(creds)

    return gc.open_by_key(
        os.environ["GOOGLE_SHEET_ID"]
    )


# ============================================================
# Watchlist
# ============================================================

def read_watchlist() -> list[dict]:
    """從 Google Sheet Watchlist 分頁讀股票清單"""

    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")

    rows = ws.get_all_records()

    enabled = [
        r
        for r in rows
        if str(r.get("enabled", "")).upper()
        in ("TRUE", "1", "YES")
    ]

    return enabled


def _ensure_watchlist_headers(ws) -> list[str]:
    """確認 Watchlist 欄位。"""

    values = ws.get_all_values()

    if not values:
        headers = [
            "stock_id",
            "name",
            "enabled",
        ]

        ws.append_row(headers)

        return headers

    headers = [
        h.strip()
        for h in values[0]
    ]

    return headers


def add_to_watchlist(
    stock_id: str,
    name: str = "",
) -> dict:
    """
    新增股票到 Watchlist。

    已存在且 enabled=FALSE：
    改回 TRUE。

    已存在且 enabled=TRUE：
    不重複新增。
    """

    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")

    headers = _ensure_watchlist_headers(ws)

    sid_col = headers.index("stock_id") + 1

    name_col = (
        headers.index("name") + 1
        if "name" in headers
        else None
    )

    en_col = headers.index("enabled") + 1

    rows = ws.get_all_records()

    for i, r in enumerate(
        rows,
        start=2,
    ):
        if (
            str(
                r.get(
                    "stock_id",
                    "",
                )
            ).strip()
            == str(stock_id).strip()
        ):
            current = str(
                r.get(
                    "enabled",
                    "",
                )
            ).upper()

            if current in (
                "TRUE",
                "1",
                "YES",
            ):
                return {
                    "status": "exists",
                    "stock_id": stock_id,
                    "name": r.get(
                        "name",
                        name,
                    ),
                }

            ws.update_cell(
                i,
                en_col,
                "TRUE",
            )

            return {
                "status": "reenabled",
                "stock_id": stock_id,
                "name": r.get(
                    "name",
                    name,
                ),
            }

    new_row = [""] * len(headers)

    new_row[
        sid_col - 1
    ] = str(stock_id)

    if name_col is not None:
        new_row[
            name_col - 1
        ] = name

    new_row[
        en_col - 1
    ] = "TRUE"

    ws.append_row(new_row)

    return {
        "status": "added",
        "stock_id": stock_id,
        "name": name,
    }


def remove_from_watchlist(
    stock_id: str,
) -> dict:
    """
    不刪除資料。
    只把 enabled 改成 FALSE。
    """

    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")

    headers = _ensure_watchlist_headers(ws)

    if "enabled" not in headers:
        return {
            "status": "no_enabled_column"
        }

    en_col = (
        headers.index(
            "enabled"
        )
        + 1
    )

    rows = ws.get_all_records()

    for i, r in enumerate(
        rows,
        start=2,
    ):
        if (
            str(
                r.get(
                    "stock_id",
                    "",
                )
            ).strip()
            == str(stock_id).strip()
        ):
            ws.update_cell(
                i,
                en_col,
                "FALSE",
            )

            return {
                "status": "disabled",
                "stock_id": stock_id,
            }

    return {
        "status": "not_found",
        "stock_id": stock_id,
    }


# ============================================================
# Signals
# ============================================================

def append_signals(
    signals: list[dict],
):
    """把每日策略結果寫入 Signals 分頁"""

    if not signals:
        return

    sh = get_gsheet()

    try:
        ws = sh.worksheet(
            "Signals"
        )

    except gspread.WorksheetNotFound:

        ws = sh.add_worksheet(
            title="Signals",
            rows=1000,
            cols=20,
        )

        ws.append_row(
            [
                "date",
                "stock_id",
                "name",
                "action",
                "signal_score",
                "entry_price",
                "stop_loss_price",
                "target_price",
                "rr_ratio",
                "position_pct",
                "winrate",
                "samples",
                "tech_signals",
                "risk_notes",
            ]
        )

    rows = []

    for s in signals:

        c = s.get(
            "components",
            {},
        )

        rows.append(
            [
                s.get(
                    "date",
                    "",
                ),

                s.get(
                    "stock_id",
                    "",
                ),

                s.get(
                    "name",
                    "",
                ),

                s.get(
                    "action",
                    "",
                ),

                s.get(
                    "signal_score",
                    "",
                ),

                s.get(
                    "entry_price",
                    "",
                ),

                s.get(
                    "stop_loss_price",
                    "",
                ),

                s.get(
                    "target_price",
                    "",
                ),

                s.get(
                    "risk_reward_ratio",
                    "",
                ),

                s.get(
                    "position_size_pct",
                    "",
                ),

                c.get(
                    "backtest_winrate",
                    "",
                ),

                c.get(
                    "backtest_samples",
                    "",
                ),

                ", ".join(
                    c.get(
                        "tech_signals",
                        [],
                    )
                ),

                " / ".join(
                    s.get(
                        "risk_notes",
                        [],
                    )
                ),
            ]
        )

    ws.append_rows(rows)


def read_latest_signals(
    limit: int = 50,
) -> list[dict]:
    """
    讀最近 N 筆 Signals。
    最新資料排在最前面。
    """

    sh = get_gsheet()

    try:
        ws = sh.worksheet(
            "Signals"
        )

    except gspread.WorksheetNotFound:
        return []

    rows = ws.get_all_records()

    if not rows:
        return []

    return rows[
        -limit:
    ][::-1]


def read_latest_signal_batch() -> list[dict]:
    """
    讀取 Signals 中
    「最新日期」的完整一批資料。

    例如：

    Signals 有
    2026-09-01 16筆
    2026-09-02 16筆

    此函式只會回傳：
    2026-09-02 的 16 筆。
    """

    sh = get_gsheet()

    try:
        ws = sh.worksheet(
            "Signals"
        )

    except gspread.WorksheetNotFound:
        return []

    rows = ws.get_all_records()

    if not rows:
        return []

    latest_date = ""

    # 從最後一筆往前找最新日期
    for r in reversed(rows):

        date_value = str(
            r.get(
                "date",
                "",
            )
        ).strip()

        if date_value:
            latest_date = date_value
            break

    if not latest_date:
        return []

    # 只取最新日期的全部股票
    latest_rows = [
        r
        for r in rows
        if str(
            r.get(
                "date",
                "",
            )
        ).strip()
        == latest_date
    ]

    return latest_rows


# ============================================================
# Performance
# ============================================================

PERFORMANCE_HEADERS = [
    "signal_date",
    "stock_id",
    "name",
    "entry_close",
    "entry_open",
    "t5_date",
    "t5_close",
    "t5_ret",
    "t10_date",
    "t10_close",
    "t10_ret",
    "t20_date",
    "t20_close",
    "t20_ret",
    "hit_target",
    "hit_stop",
    "status",
]


def read_performance() -> list[dict]:
    """
    讀 Performance 分頁所有追蹤紀錄。
    """

    sh = get_gsheet()

    try:
        ws = sh.worksheet(
            "Performance"
        )

    except gspread.WorksheetNotFound:
        return []

    return ws.get_all_records()


def write_performance(
    records: list[dict],
):
    """
    重寫 Performance 分頁。
    """

    sh = get_gsheet()

    try:

        ws = sh.worksheet(
            "Performance"
        )

        ws.clear()

    except gspread.WorksheetNotFound:

        rows_alloc = max(
            2000,
            len(records) + 100,
        )

        ws = sh.add_worksheet(
            title="Performance",
            rows=rows_alloc,
            cols=len(
                PERFORMANCE_HEADERS
            ),
        )

    ws.append_row(
        PERFORMANCE_HEADERS
    )

    if not records:
        return

    rows = [
        [
            r.get(
                h,
                "",
            )
            for h in PERFORMANCE_HEADERS
        ]
        for r in records
    ]

    ws.append_rows(rows)