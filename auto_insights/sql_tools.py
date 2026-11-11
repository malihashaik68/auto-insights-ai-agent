"""SQL layer: loads the (cleaned) dataset into an in-memory SQLite database
so KPIs can be computed with SQL, by the analyst or by the agent.

Queries run under a SQLite authorizer that allows reading only, so a query
written by the LLM cannot modify, drop or attach anything.
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd

TABLE = "readings"
MAX_ROWS = 200

_READ_ONLY_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                      sqlite3.SQLITE_RECURSIVE}


def sql_name(column: str) -> str:
    """'LV ActivePower (kW)' -> 'lv_activepower_kw'"""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", column).strip("_").lower()
    return name if name and not name[0].isdigit() else f"c_{name}"


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


class SQLWarehouse:
    """In-memory SQLite copy of a DataFrame with snake_case column names."""

    def __init__(self, df: pd.DataFrame):
        self.columns = {c: sql_name(c) for c in df.columns}
        data = df.rename(columns=self.columns)
        for col in data.select_dtypes(include=["datetime", "datetimetz"]).columns:
            data[col] = data[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        for col in data.select_dtypes(include="bool").columns:
            data[col] = data[col].astype(int)
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        data.to_sql(TABLE, self.conn, index=False)
        self.conn.set_authorizer(_authorizer)  # from here on: read-only

    def schema(self) -> dict:
        cur = self.conn.execute(f"SELECT * FROM {TABLE} LIMIT 0")
        return {"table": TABLE, "columns": [d[0] for d in cur.description],
                "original_names": self.columns,
                "notes": "Timestamps are TEXT 'YYYY-MM-DD HH:MM:SS'; use strftime() to group. "
                         "Flag columns are 0/1."}

    def query(self, sql: str) -> pd.DataFrame:
        statement = sql.strip().rstrip(";")
        if ";" in statement:
            raise ValueError("Only one SQL statement is allowed.")
        if not re.match(r"^\s*(select|with)\b", statement, re.I):
            raise ValueError("Only SELECT queries are allowed.")
        try:
            return pd.read_sql_query(statement, self.conn)
        except Exception as exc:  # sqlite errors -> readable message for the agent / UI
            raise ValueError(f"SQL error: {exc}") from None
