"""
services/utils.py — Shared utilities used by both routers/ai.py and services/agent.py

Breaks the circular import:
  Before: ai.py → agent.py → ai.py (circular)
  After:  ai.py → utils.py ← agent.py (no cycle)

Contains:
  - Groq client + call_groq()
  - SQL safety + extraction
  - DuckDB execution
  - Schema text builder
"""

import os
import re
import uuid
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
from groq import Groq
from dotenv import load_dotenv
from fastapi import HTTPException

from services.sql_validator import validate_sql_ast, estimate_query_complexity

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# ── Lazy Groq client ──────────────────────────────────────────
_groq_client = None


def get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise HTTPException(
                status_code=503,
                detail="Groq API key not configured. Set GROQ_API_KEY in .env",
            )
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def call_groq(messages: list, max_tokens: int = 1024, temperature: float = 0.1) -> str:
    """Call Groq API and return response text."""
    client = get_groq_client()
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.9,
        )
        if not response.choices:
            raise RuntimeError("Groq returned empty response — no choices in reply")
        content = response.choices[0].message.content
        return content.strip() if content else ""

    except Exception as e:
        err = str(e).lower()
        # BUG FIX: agent nodes catch all Exception subclasses.
        # Raising HTTPException inside an agent node caused it to be
        # swallowed silently, leaving state["error"] empty and the
        # pipeline continuing with broken/empty SQL.
        # RuntimeError propagates correctly through agent try/except blocks
        # and surfaces as state["error"] to the final_agent.
        if "authentication" in err or "api key" in err:
            raise RuntimeError("Groq API key invalid or missing — check GROQ_API_KEY in .env")
        if "rate limit" in err:
            raise RuntimeError("Groq rate limit reached — please wait and retry")
        raise RuntimeError(f"Groq API error: {str(e)}")


def is_safe_sql(sql: str) -> bool:
    """AST-based SQL validation. Falls back to string-based if sqlglot unavailable."""
    valid, _ = validate_sql_ast(sql)
    return valid


def extract_sql_from_response(response_text: str) -> Optional[str]:
    """Extract SQL from AI response — handles code blocks and raw SQL."""
    pattern_sql = re.search(r"```sql\s*(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
    if pattern_sql:
        return pattern_sql.group(1).strip()

    pattern_generic = re.search(r"```\s*(.*?)```", response_text, re.DOTALL)
    if pattern_generic:
        candidate = pattern_generic.group(1).strip()
        if candidate.lower().startswith("select") or candidate.lower().startswith("with"):
            return candidate

    pattern_select = re.search(
        r"((?:with\s+\w+.*?as\s*\(.*?\)\s*)?select\s+.*?;?)",
        response_text,
        re.DOTALL | re.IGNORECASE,
    )
    if pattern_select:
        return pattern_select.group(1).strip()

    return None


def build_schema_text(dataset) -> str:
    """
    Build schema string for SQL prompt.
    Format modelled after reference implementation (ai_data_science_team):
    - Prominently lists table names and row counts
    - Includes DuckDB dialect notice so LLM generates correct syntax
    - Quotes column names with spaces
    - Shows sample values so LLM uses correct WHERE clause values
    """
    lines = [
        f"Database dialect: DuckDB",
        f"Dataset: {dataset.dataset_name}",
    ]
    # Include user-provided dataset description if available
    if getattr(dataset, "description", None):
        lines.append(f"Description: {dataset.description}")
    for table in dataset.tables:
        row_str = f"{table.row_count:,}" if table.row_count else "unknown"
        lines.append(f"\nTable: {table.table_name}  ({row_str} rows)")
        lines.append("Columns:")
        for col in table.columns:
            null_str  = "nullable" if col.is_nullable else "not null"
            # Quote column names with spaces for SQL generation accuracy
            col_name  = f'"{col.column_name}"' if " " in col.column_name else col.column_name
            # Truncate sample_values to 120 chars — prevents token explosion
            sample_part = ""
            if col.sample_values:
                truncated = col.sample_values[:120]
                if len(col.sample_values) > 120:
                    truncated += "..."
                sample_part = f"  -- sample values: {truncated}"
            lines.append(f"  {col_name}  {col.data_type}  {null_str}{sample_part}")
    return "\n".join(lines)


def safe_identifier(name: str) -> str:
    """Validate table/view names before injecting into DuckDB SQL."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        raise HTTPException(
            400,
            f"Invalid table name '{name}' — only letters, numbers, spaces and underscores allowed",
        )
    return name


def execute_sql_duckdb(sql: str, dataset) -> pd.DataFrame:
    """
    Execute SQL on uploaded CSV/Excel files using DuckDB.
    Memory limited, thread limited, auto LIMIT injected.
    """
    if not dataset.file_path:
        raise HTTPException(422, "Dataset has no file path — cannot execute SQL")

    file_path = Path(dataset.file_path)
    if not file_path.exists():
        # Raise RuntimeError so execute_sql_agent surfaces a clear message.
        # This happens when the server was restarted and the uploads/ folder
        # was cleared, or the file was manually deleted.
        raise RuntimeError(
            f"Dataset file not found on disk: {file_path}. "
            "The file may have been deleted. Please re-upload your dataset."
        )

    # Complexity check
    total_rows = sum(t.row_count or 0 for t in dataset.tables)
    acceptable, reason = estimate_query_complexity(sql, total_rows)
    if not acceptable:
        raise HTTPException(422, reason)

    # WINDOWS FIX: DuckDB cannot handle Windows backslash paths (D:\folder\file.csv).
    # Backslashes are treated as escape sequences and crash DuckDB instantly (16ms failure).
    # .as_posix() converts to forward slashes: D:/folder/file.csv — works on all platforms.
    duckdb_path = file_path.as_posix()

    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute("SET memory_limit='512MB'")
        conn.execute("SET threads=2")

        for table in dataset.tables:
            suffix     = file_path.suffix.lower()
            table_name = safe_identifier(table.table_name)

            if suffix == ".csv":
                # DuckDB CREATE VIEW does not support prepared parameters ($1).
                # File path is server-controlled (not user input), so safe to inline.
                conn.execute(
                    f'CREATE VIEW "{table_name}" AS '
                    f"SELECT * FROM read_csv_auto('{duckdb_path}')"
                )
            elif suffix in (".xlsx", ".xls"):
                try:
                    df_sheet = pd.read_excel(file_path, sheet_name=table.table_name)
                except ValueError:
                    raise HTTPException(
                        422, f"Sheet '{table.table_name}' not found in Excel file"
                    )
                conn.register(table_name, df_sheet)
            else:
                raise HTTPException(422, f"Unsupported file type: {suffix}")

        # Inject LIMIT if missing
        sql_check = sql.lower().strip()
        if "limit" not in sql_check and "count(" not in sql_check:
            sql = sql.rstrip(";").rstrip() + " LIMIT 10000"

        result_df = conn.execute(sql).df()
        return result_df

    except HTTPException:
        raise
    except duckdb.Error as e:
        raise RuntimeError(f"SQL execution error: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {str(e)}")
    finally:
        conn.close()