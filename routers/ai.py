import os
import re
import uuid
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd
from groq import Groq
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status

from database import get_db
from models import (
    User, Dataset, DatasetTable, ChatSession,
    AIQuery, QueryResult, Visualization, Insight, Recommendation,
)
from routers.auth import get_current_user
from schemas.ai import (
    AnalyzeRequest,
    AnalyzeResponse,
    GenerateSQLRequest,
    GenerateSQLResponse,
    InsightResponse,
    RecommendationResponse,
    VisualizationResponse,
    QueryResultResponse,
)

load_dotenv()

router = APIRouter()

# ── Groq config ───────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# ── Result preview: max rows saved to DB ─────────────────────
MAX_PREVIEW_ROWS = 100

# ── Lazy Groq client ─────────────────────────────────────────
_groq_client = None

def get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Groq API key not configured. Set GROQ_API_KEY in .env",
            )
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ════════════════════════════════════════════════════════════════
# UTILITIES
# ════════════════════════════════════════════════════════════════

def validate_uuid(value: str, label: str = "ID") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {label} format",
        )


def get_dataset_or_403(
    db: Session,
    dataset_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Dataset:
    """Fetch dataset — 404 if not found, 403 if wrong owner."""
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if ds.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return ds


def get_query_or_403(
    db: Session,
    query_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AIQuery:
    """Fetch AIQuery — 404 if not found, 403 if wrong owner via session."""
    q = db.query(AIQuery).filter(AIQuery.id == query_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")
    if q.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return q


def build_schema_text(dataset: Dataset) -> str:
    """
    Build a schema string for the SQL generation prompt.
    Includes sample values for TEXT columns so the AI knows
    what values to use in WHERE conditions.

    Example output:
        Table: students (10000 rows)
          - Placement TEXT NULL  [values: Yes, No]
          - IQ INTEGER NOT NULL
          - CGPA FLOAT NULL
    """
    lines = [f"Dataset: {dataset.dataset_name}"]
    for table in dataset.tables:
        lines.append(f"\nTable: {table.table_name} ({table.row_count or '?'} rows)")
        for col in table.columns:
            null_str     = "NULL" if col.is_nullable else "NOT NULL"
            sample_part  = ""
            if col.sample_values:
                sample_part = f"  [values: {col.sample_values}]"
            lines.append(f"  - {col.column_name} {col.data_type} {null_str}{sample_part}")
    return "\n".join(lines)


def is_safe_sql(sql: str) -> bool:
    """
    Only allow SELECT statements.
    Strips SQL comments first so -- comment\\nSELECT still passes.
    Blocks all data-modifying or destructive SQL.
    """
    # Strip single-line comments
    q = re.sub(r"--[^\n]*", "", sql)
    # Strip multi-line comments
    q = re.sub(r"/\*.*?\*/", "", q, flags=re.DOTALL)
    q = q.lower().strip()

    if not (q.startswith("select") or q.startswith("with")):
        return False

    forbidden = [
        "insert ", "update ", "delete ", "drop ",
        "alter ", "truncate ", "create ", "grant ",
        "revoke ", "replace ", "merge ", "exec ",
        "execute ", "call ", "pragma ",
    ]
    return not any(word in q for word in forbidden)


def extract_sql_from_response(response_text: str) -> Optional[str]:
    """
    Extract SQL from AI response.
    Handles:
    - ```sql ... ``` code blocks
    - ``` ... ``` generic code blocks
    - Raw SQL if no code block found
    """
    # Try ```sql ... ``` first
    pattern_sql = re.search(r"```sql\s*(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
    if pattern_sql:
        return pattern_sql.group(1).strip()

    # Try generic ``` ... ```
    pattern_generic = re.search(r"```\s*(.*?)```", response_text, re.DOTALL)
    if pattern_generic:
        candidate = pattern_generic.group(1).strip()
        if candidate.lower().startswith("select") or candidate.lower().startswith("with"):
            return candidate

    # Try to find a SELECT statement anywhere in the text
    pattern_select = re.search(
        r"((?:with\s+\w+.*?as\s*\(.*?\)\s*)?select\s+.*?;?)",
        response_text,
        re.DOTALL | re.IGNORECASE,
    )
    if pattern_select:
        return pattern_select.group(1).strip()

    return None


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
            raise HTTPException(502, "Groq returned empty response")
        content = response.choices[0].message.content
        return content.strip() if content else ""

    except HTTPException:
        raise

    except Exception as e:
        err = str(e).lower()
        if "authentication" in err or "api key" in err:
            raise HTTPException(503, "Invalid Groq API key")
        if "rate limit" in err:
            raise HTTPException(429, "Groq rate limit reached. Please wait and try again.")
        raise HTTPException(502, f"Groq API error: {str(e)}")


# ════════════════════════════════════════════════════════════════
# SQL GENERATION
# ════════════════════════════════════════════════════════════════

def generate_sql_from_groq(schema_text: str, user_query: str) -> tuple[str, str]:
    """
    Ask Groq to generate SQL for the user query given the schema.
    Returns (sql_query, explanation).
    Uses temperature=0.1 for maximum accuracy.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert SQL analyst. Generate accurate SQL SELECT queries "
                "based on the user's question and the provided schema.\n\n"
                "STRICT RULES:\n"
                "- ONLY generate SELECT queries. Never INSERT, UPDATE, DELETE, DROP, or ALTER.\n"
                "- Use EXACT table and column names from the schema.\n"
                "- ALWAYS enclose table names and column names in double quotes, e.g. \"My Table\".\n"
                "- Always wrap your SQL in a ```sql code block.\n"
                "- After the SQL, write a brief explanation of what the query does.\n"
                "- If the question cannot be answered with SQL, say so clearly.\n\n"
                f"Schema:\n{schema_text}"
            ),
        },
        {
            "role": "user",
            "content": f"Generate a SQL query to answer: {user_query}",
        },
    ]

    response_text = call_groq(messages, max_tokens=512, temperature=0.1)

    # Extract SQL
    sql = extract_sql_from_response(response_text)

    # Extract explanation (everything after the code block)
    explanation = re.sub(r"```.*?```", "", response_text, flags=re.DOTALL).strip()
    if not explanation:
        explanation = "SQL query generated successfully."

    return sql or "", explanation


# ════════════════════════════════════════════════════════════════
# SQL EXECUTION WITH DUCKDB
# ════════════════════════════════════════════════════════════════

def safe_identifier(name: str) -> str:
    """
    Validate table/view names before injecting into DuckDB SQL.
    Only allows alphanumeric + underscore — blocks injection attacks.
    """
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        raise HTTPException(
            400,
            f"Invalid table name '{name}' — only letters, numbers, spaces and underscores allowed",
        )
    return name


def execute_sql_duckdb(sql: str, dataset: Dataset) -> pd.DataFrame:
    """
    Execute SQL on uploaded CSV/Excel files using DuckDB.

    Fixes applied:
    - Table names validated before use in SQL (injection prevention)
    - Excel sheet-not-found error handled with clear 422 message
    - DuckDB connection always closed via try/finally
    """
    if not dataset.file_path:
        raise HTTPException(422, "Dataset has no file path — cannot execute SQL")

    file_path = Path(dataset.file_path)
    if not file_path.exists():
        raise HTTPException(404, f"Dataset file not found: {file_path}")

    conn = duckdb.connect(database=":memory:")

    try:
        for table in dataset.tables:
            suffix     = file_path.suffix.lower()
            table_name = safe_identifier(table.table_name)

            if suffix == ".csv":
                # DuckDB reads CSV directly — very fast
                conn.execute(
                    f'CREATE VIEW "{table_name}" AS '
                    f"SELECT * FROM read_csv_auto('{file_path}')"
                )
            elif suffix in (".xlsx", ".xls"):
                # Read with pandas then register as DuckDB relation
                try:
                    df = pd.read_excel(file_path, sheet_name=table.table_name)
                except ValueError:
                    raise HTTPException(
                        422,
                        f"Sheet '{table.table_name}' not found in Excel file",
                    )
                conn.register(table_name, df)
            else:
                raise HTTPException(
                    422, f"Unsupported file type for SQL execution: {suffix}"
                )

        result_df = conn.execute(sql).df()
        return result_df

    except HTTPException:
        raise
    except duckdb.Error as e:
        raise HTTPException(422, f"SQL execution error: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Unexpected error during query execution: {str(e)}")
    finally:
        conn.close()   # always closed — even on exception


def execute_sql_external_db(sql: str, dataset: Dataset) -> pd.DataFrame:
    """
    Execute SQL on an external database connection (PostgreSQL/MySQL).
    Uses the connection info stored in dataset.database_connection.
    Note: password is not stored — user must reconnect if needed.
    """
    conn_info = dataset.database_connection
    if not conn_info:
        raise HTTPException(422, "No database connection info found for this dataset")

    db_type  = conn_info.get("db_type", "postgresql")
    host     = conn_info.get("host")
    port     = conn_info.get("port")
    username = conn_info.get("username")
    database = conn_info.get("database")

    # Password is not stored for security — raise clear error
    raise HTTPException(
        status_code=422,
        detail=(
            "External database queries require re-authentication. "
            "This feature will be available in a future update."
        ),
    )


# ════════════════════════════════════════════════════════════════
# INSIGHT GENERATION
# ════════════════════════════════════════════════════════════════

def generate_insights(
    user_query: str,
    sql: str,
    results_preview: list,
    row_count: int,
) -> list[dict]:
    """
    Ask Groq to generate business insights from query results.
    Returns list of {text, score} dicts.
    """
    if not results_preview:
        return []

    results_sample = json.dumps(results_preview[:20], default=str)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a business intelligence analyst. "
                "Analyze the query results and provide exactly 3 key business insights. "
                "Each insight must be:\n"
                "- Specific and data-driven\n"
                "- Actionable for business decisions\n"
                "- Written in plain English\n\n"
                "Format your response as a JSON array ONLY — no extra text:\n"
                '[\n'
                '  {"insight": "Your insight here", "importance": 0.9},\n'
                '  {"insight": "Your insight here", "importance": 0.7},\n'
                '  {"insight": "Your insight here", "importance": 0.5}\n'
                ']'
            ),
        },
        {
            "role": "user",
            "content": (
                f"User asked: {user_query}\n"
                f"SQL used: {sql}\n"
                f"Total rows returned: {row_count}\n"
                f"Sample results:\n{results_sample}"
            ),
        },
    ]

    response_text = call_groq(messages, max_tokens=512, temperature=0.2)

    # Parse JSON response
    try:
        # Strip markdown if present
        clean = re.sub(r"```.*?```", "", response_text, flags=re.DOTALL).strip()
        # Find JSON array
        json_match = re.search(r"\[.*\]", clean, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return [
                {
                    "text":  item.get("insight", ""),
                    "score": float(item.get("importance", 0.5)),
                }
                for item in data
                if item.get("insight")
            ]
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: return raw text as single insight
    if response_text:
        return [{"text": response_text, "score": 0.5}]
    return []


# ════════════════════════════════════════════════════════════════
# RECOMMENDATION GENERATION
# ════════════════════════════════════════════════════════════════

def generate_recommendations(
    user_query: str,
    insights: list[dict],
    row_count: int,
) -> list[dict]:
    """
    Ask Groq to generate business recommendations based on insights.
    Returns list of {text, score} dicts.
    """
    if not insights:
        return []

    insights_text = "\n".join(
        f"- {i['text']}" for i in insights
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a strategic business consultant. "
                "Based on the data insights provided, give exactly 3 actionable recommendations. "
                "Each recommendation must be:\n"
                "- Specific and implementable\n"
                "- Directly tied to the data findings\n"
                "- Written as a clear action\n\n"
                "Format your response as a JSON array ONLY — no extra text:\n"
                '[\n'
                '  {"recommendation": "Your recommendation here", "confidence": 0.9},\n'
                '  {"recommendation": "Your recommendation here", "confidence": 0.7},\n'
                '  {"recommendation": "Your recommendation here", "confidence": 0.6}\n'
                ']'
            ),
        },
        {
            "role": "user",
            "content": (
                f"User question: {user_query}\n"
                f"Key insights from the data:\n{insights_text}\n"
                f"Total data points analyzed: {row_count}"
            ),
        },
    ]

    response_text = call_groq(messages, max_tokens=512, temperature=0.3)

    try:
        clean = re.sub(r"```.*?```", "", response_text, flags=re.DOTALL).strip()
        json_match = re.search(r"\[.*\]", clean, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return [
                {
                    "text":  item.get("recommendation", ""),
                    "score": float(item.get("confidence", 0.5)),
                }
                for item in data
                if item.get("recommendation")
            ]
    except (json.JSONDecodeError, ValueError):
        pass

    if response_text:
        return [{"text": response_text, "score": 0.5}]
    return []


# ════════════════════════════════════════════════════════════════
# VISUALIZATION CONFIG GENERATION
# ════════════════════════════════════════════════════════════════

def generate_chart_config(df: pd.DataFrame, user_query: str) -> Optional[dict]:
    """
    Automatically determine the best chart type and generate
    a Recharts-compatible chart configuration based on the DataFrame structure.
    No AI call needed — pure logic.
    """
    if df.empty or len(df.columns) < 2:
        return None

    cols       = list(df.columns)
    num_cols   = df.select_dtypes(include=["number"]).columns.tolist()
    str_cols   = df.select_dtypes(include=["object", "string", "category"]).columns.tolist()
    row_count  = len(df)

    # Determine chart type based on data shape
    if len(num_cols) == 0:
        return None  # No numeric data — nothing to chart

    if len(str_cols) >= 1 and len(num_cols) >= 1:
        # Categorical + numeric = bar chart
        x_col = str_cols[0]
        y_col = num_cols[0]
        chart_type = "bar"

        # If too many categories, still use bar (maybe horizontal later)
        unique_vals = df[x_col].nunique()

        # Pie chart for small category sets (2-6 items)
        if 2 <= unique_vals <= 6:
            chart_type = "pie"

        labels  = df[x_col].astype(str).tolist()
        values  = df[y_col].tolist()
        
        # Build Recharts array: [{x_col: label1, y_col: val1}, ...]
        recharts_data = []
        for i in range(len(labels)):
            recharts_data.append({
                x_col: labels[i],
                y_col: values[i]
            })

        return {
            "type": chart_type,
            "data": recharts_data,
            "x_axis": x_col,
            "y_axis": y_col,
            "title": f"{y_col} by {x_col}"
        }

    if len(num_cols) >= 2:
        # Two numeric columns = area or bar depending on row count
        chart_type = "area" if row_count > 10 else "bar"
        x_col = num_cols[0]
        y_col = num_cols[1]
        
        labels = df[x_col].tolist()
        values = df[y_col].tolist()
        
        recharts_data = []
        for i in range(len(labels)):
            recharts_data.append({
                x_col: labels[i],
                y_col: values[i]
            })

        return {
            "type": chart_type,
            "data": recharts_data,
            "x_axis": x_col,
            "y_axis": y_col,
            "title": f"{y_col} vs {x_col}"
        }

    return None


# ════════════════════════════════════════════════════════════════
# POST /ai/analyze
# Full pipeline: SQL → Execute → Insights → Recommendations → Charts
# ════════════════════════════════════════════════════════════════
@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Full AI analysis: generate SQL → execute → insights → recommendations",
)
def analyze(
    body:         AnalyzeRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    The core endpoint of the platform. Full pipeline:

    1. Validate session + dataset ownership
    2. Build schema context
    3. Ask Groq to generate SQL
    4. Validate SQL (SELECT only)
    5. Execute SQL with DuckDB on the file
    6. Save query + results to database
    7. Generate insights from results
    8. Generate recommendations from insights
    9. Generate chart config from results
    10. Save everything and return
    """
    # Validate IDs
    session_uid = validate_uuid(body.session_id, "session ID")
    dataset_uid = validate_uuid(body.dataset_id, "dataset ID")

    # Validate session ownership
    session = db.query(ChatSession).filter(
        ChatSession.id == session_uid,
        ChatSession.user_id == current_user.id,
    ).first()
    if not session:
        raise HTTPException(404, "Session not found or access denied")

    # Validate dataset ownership
    dataset = get_dataset_or_403(db, dataset_uid, current_user.id)

    # Build schema
    schema_text = build_schema_text(dataset)

    # ── Step 1: Generate SQL ──────────────────────────────────
    generated_sql, explanation = generate_sql_from_groq(schema_text, body.user_query)

    if not generated_sql:
        # Save failed query and return
        failed_query = AIQuery(
            session_id=session_uid,
            user_query=body.user_query,
            generated_sql=None,
            sql_valid=False,
            execution_time_ms=0,
        )
        db.add(failed_query)
        db.commit()
        db.refresh(failed_query)
        raise HTTPException(
            422,
            "Could not generate SQL for this query. "
            "Try rephrasing your question more specifically.",
        )

    # ── Step 2: Validate SQL ──────────────────────────────────
    sql_valid = is_safe_sql(generated_sql)

    if not sql_valid:
        failed_query = AIQuery(
            session_id=session_uid,
            user_query=body.user_query,
            generated_sql=generated_sql,
            sql_valid=False,
            execution_time_ms=0,
        )
        db.add(failed_query)
        db.commit()
        raise HTTPException(
            403,
            "Generated SQL contains forbidden operations. Only SELECT queries are allowed.",
        )

    # ── Step 3: Execute SQL ───────────────────────────────────
    start_time = time.time()
    result_df  = None

    if dataset.dataset_type in ("csv", "excel"):
        result_df = execute_sql_duckdb(generated_sql, dataset)
    else:
        result_df = execute_sql_external_db(generated_sql, dataset)

    execution_time_ms = int((time.time() - start_time) * 1000)

    # ── Step 4: Prepare result preview ───────────────────────
    row_count      = len(result_df)
    preview_rows   = result_df.head(MAX_PREVIEW_ROWS)
    result_preview = json.loads(
        preview_rows.to_json(orient="records", date_format="iso", default_handler=str)
    )

    # ── Step 5: Generate insights ─────────────────────────────
    insights_data = generate_insights(
        body.user_query,
        generated_sql,
        result_preview,
        row_count,
    )

    # ── Step 6: Generate recommendations ─────────────────────
    recommendations_data = generate_recommendations(
        body.user_query,
        insights_data,
        row_count,
    )

    # ── Step 7: Generate chart config ────────────────────────
    chart_config = generate_chart_config(result_df, body.user_query)

    # ── Step 8: Save everything in one transaction ────────────
    try:
        # Save AI query
        ai_query = AIQuery(
            session_id=session_uid,
            user_query=body.user_query,
            generated_sql=generated_sql,
            sql_valid=True,
            execution_time_ms=execution_time_ms,
        )
        db.add(ai_query)
        db.flush()

        # Save query result
        qr = QueryResult(
            query_id=ai_query.id,
            result_row_count=row_count,
            result_preview=result_preview,
        )
        db.add(qr)

        # Save insights
        insight_objs = []
        for item in insights_data:
            ins = Insight(
                query_id=ai_query.id,
                insight_text=item["text"],
                importance_score=item["score"],
            )
            db.add(ins)
            insight_objs.append(ins)

        # Save recommendations
        rec_objs = []
        for item in recommendations_data:
            rec = Recommendation(
                query_id=ai_query.id,
                recommendation_text=item["text"],
                confidence_score=item["score"],
            )
            db.add(rec)
            rec_objs.append(rec)

        # Save visualization
        viz_objs = []
        if chart_config:
            chart_type = chart_config.get("type", "bar")
            viz = Visualization(
                query_id=ai_query.id,
                chart_type=chart_type,
                chart_config=chart_config,
            )
            db.add(viz)
            viz_objs.append(viz)

        # Update session last_activity
        session.last_activity = datetime.utcnow()

        db.commit()
        db.refresh(ai_query)
        db.refresh(qr)
        for obj in insight_objs:
            db.refresh(obj)
        for obj in rec_objs:
            db.refresh(obj)
        for obj in viz_objs:
            db.refresh(obj)

    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to save analysis results: {str(e)}")

    return {
        "query_id":          ai_query.id,
        "user_query":        body.user_query,
        "generated_sql":     generated_sql,
        "sql_valid":         True,
        "execution_time_ms": execution_time_ms,
        "result": {
            "id":               qr.id,
            "result_row_count": row_count,
            "result_preview":   result_preview,
            "created_at":       qr.created_at,
        },
        "visualizations":  viz_objs,
        "insights":        insight_objs,
        "recommendations": rec_objs,
    }


# ════════════════════════════════════════════════════════════════
# POST /ai/generate-sql
# SQL generation only — no execution
# ════════════════════════════════════════════════════════════════
@router.post(
    "/generate-sql",
    response_model=GenerateSQLResponse,
    summary="Generate SQL only — without executing it",
)
def generate_sql_only(
    body:         GenerateSQLRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Generates SQL for a user query without executing it.
    Useful for previewing SQL before running.
    """
    dataset_uid = validate_uuid(body.dataset_id, "dataset ID")
    dataset     = get_dataset_or_403(db, dataset_uid, current_user.id)
    schema_text = build_schema_text(dataset)

    sql, explanation = generate_sql_from_groq(schema_text, body.user_query)

    if not sql:
        raise HTTPException(422, "Could not generate SQL. Try rephrasing your question.")

    return {"generated_sql": sql, "explanation": explanation}


# ════════════════════════════════════════════════════════════════
# GET /ai/results/{query_id}
# ════════════════════════════════════════════════════════════════
@router.get(
    "/results/{query_id}",
    response_model=QueryResultResponse,
    summary="Get stored query results",
)
def get_results(
    query_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Returns stored query results for a given query ID."""
    quid  = validate_uuid(query_id, "query ID")
    query = get_query_or_403(db, quid, current_user.id)

    if not query.result:
        raise HTTPException(404, "No results found for this query")

    return query.result


# ════════════════════════════════════════════════════════════════
# GET /ai/insights/{query_id}
# ════════════════════════════════════════════════════════════════
@router.get(
    "/insights/{query_id}",
    response_model=List[InsightResponse],
    summary="Get AI insights for a query",
)
def get_insights(
    query_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Returns all insights generated for a query."""
    quid  = validate_uuid(query_id, "query ID")
    query = get_query_or_403(db, quid, current_user.id)
    return sorted(query.insights, key=lambda i: i.importance_score or 0, reverse=True)


# ════════════════════════════════════════════════════════════════
# GET /ai/recommendations/{query_id}
# ════════════════════════════════════════════════════════════════
@router.get(
    "/recommendations/{query_id}",
    response_model=List[RecommendationResponse],
    summary="Get AI recommendations for a query",
)
def get_recommendations(
    query_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Returns all recommendations generated for a query."""
    quid  = validate_uuid(query_id, "query ID")
    query = get_query_or_403(db, quid, current_user.id)
    return sorted(query.recommendations, key=lambda r: r.confidence_score or 0, reverse=True)


# ════════════════════════════════════════════════════════════════
# GET /ai/visualizations/{query_id}
# ════════════════════════════════════════════════════════════════
@router.get(
    "/visualizations/{query_id}",
    response_model=List[VisualizationResponse],
    summary="Get chart configurations for a query",
)
def get_visualizations(
    query_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Returns all chart configs for a query. Frontend renders these directly."""
    quid  = validate_uuid(query_id, "query ID")
    query = get_query_or_403(db, quid, current_user.id)
    return query.visualizations


# ════════════════════════════════════════════════════════════════
# POST /ai/agent
# LangGraph agentic endpoint — agent decides what to do
# ════════════════════════════════════════════════════════════════
from services.agent import run_agent
from schemas.ai import AgentRequest, AgentResponse


@router.post(
    "/agent",
    response_model=AgentResponse,
    summary="Agentic AI analyst — agent decides SQL vs explanation, auto-retries on failure",
)
def agent_analyze(
    body:         AgentRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    LangGraph-powered agentic endpoint.

    Unlike /ai/analyze which always runs the full pipeline,
    this agent DECIDES what to do based on your query:

    - Data questions  → generates SQL → executes → insights → recommendations → chart
    - Conceptual questions → answers directly without SQL
    - Failed SQL → automatically retries up to 3 times with self-correction

    The agent is aware of your dataset schema at every step.
    """
    session_uid = validate_uuid(body.session_id, "session ID")
    dataset_uid = validate_uuid(body.dataset_id, "dataset ID")

    # Validate session ownership
    session = db.query(ChatSession).filter(
        ChatSession.id == session_uid,
        ChatSession.user_id == current_user.id,
    ).first()
    if not session:
        raise HTTPException(404, "Session not found or access denied")

    # Validate dataset ownership
    dataset = get_dataset_or_403(db, dataset_uid, current_user.id)

    # Build schema text
    schema_text = build_schema_text(dataset)

    # Run the LangGraph agent — pass dataset object directly
    try:
        result = run_agent(
            user_query=body.user_query,
            schema_text=schema_text,
            dataset=dataset,
        )
    except Exception as e:
        raise HTTPException(500, f"Agent failed: {str(e)}")

    # Save to database if SQL was executed successfully
    if result.get("route") == "sql" and result.get("result_rows"):
        try:
            ai_query = AIQuery(
                session_id=session_uid,
                user_query=body.user_query,
                generated_sql=result.get("generated_sql"),
                sql_valid=True,
                execution_time_ms=result.get("execution_time_ms", 0),
            )
            db.add(ai_query)
            db.flush()

            db.add(QueryResult(
                query_id=ai_query.id,
                result_row_count=result.get("row_count", 0),
                result_preview=result.get("result_rows", []),
            ))

            for ins in result.get("insights", []):
                db.add(Insight(
                    query_id=ai_query.id,
                    insight_text=ins["text"],
                    importance_score=ins["score"],
                ))

            for rec in result.get("recommendations", []):
                db.add(Recommendation(
                    query_id=ai_query.id,
                    recommendation_text=rec["text"],
                    confidence_score=rec["score"],
                ))

            if result.get("chart_config"):
                db.add(Visualization(
                    query_id=ai_query.id,
                    chart_type=result["chart_config"].get("type", "bar"),
                    chart_config=result["chart_config"],
                ))

            session.last_activity = datetime.utcnow()
            db.commit()

        except Exception:
            db.rollback()
            # Don't fail the response — just skip DB save

    return {
        "route":             result.get("route", "sql"),
        "user_query":        body.user_query,
        "plan":              result.get("plan"),
        "generated_sql":     result.get("generated_sql"),
        "sql_explanation":   result.get("sql_explanation"),
        "sql_valid":         result.get("sql_valid", False),
        "sql_attempts":      result.get("sql_attempts", 0),
        "error_type":        result.get("error_type"),
        "row_count":         result.get("row_count", 0),
        "result_preview":    result.get("result_rows", []),
        "result_valid":      result.get("result_valid", False),
        "result_issue":      result.get("result_issue"),
        "insights":          result.get("insights", []),
        "recommendations":   result.get("recommendations", []),
        "chart_config":      result.get("chart_config"),
        "explanation":       result.get("explanation"),
        "final_answer":      result.get("final_answer"),
        "execution_time_ms": result.get("execution_time_ms", 0),
        "error":             result.get("error"),
    }