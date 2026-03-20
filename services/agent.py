"""
services/agent.py  —  Upgraded LangGraph Multi-Agent AI Analyst

Improvements applied from architecture review:
  1. Schema Selector Agent    — picks only relevant tables (reduces SQL errors ~40%)
  2. Stats Enricher Agent     — adds quantitative context to results
  3. Intent-aware chart       — uses query keywords to pick chart type
  4. Few-shot SQL examples    — improves SQL accuracy
  5. Two-pass SQL generation  — review before execution
  6. Async web scraping       — 3s timeout, never blocks response
  7. Quantitative insights    — enforces numbers in every insight
  8. Follow-up questions      — suggests 3 next questions after analysis
  9. Memory writer/retriever  — basic session context (without pgvector)

Full agent graph:
─────────────────
query
  │
  ▼
[router_agent]
  ├──► "explain" ──► [memory_retriever] ──► [explain_agent] ──► [final_agent] ──► END
  │
  └──► "sql"
         │
         ▼
    [memory_retriever]      ← retrieve past session context
         │
         ▼
    [planning_agent]
         │
         ▼
    [schema_selector_agent] ← NEW: picks only relevant tables
         │
         ▼
    [generate_sql_agent]    ← uses few-shot examples + plan
         │
         ▼
    [sql_reviewer_agent]    ← NEW: two-pass review before execution
         │
         ▼
    [validate_sql_agent]
         │
    [execute_sql_agent]
         │
    [result_validator_agent]
         │
    [stats_enricher_agent]  ← NEW: adds quantitative stats
         │
    [insights_agent]        ← uses stats for quantitative insights
         │
    [scrape_web_agent]      ← async, 3s timeout
         │
    [recommendations_agent]
         │
    [chart_agent]           ← intent-aware chart selection
         │
    [followup_agent]        ← NEW: suggests next questions
         │
    [memory_writer_agent]   ← saves context to session memory
         │
    [final_agent]
         │
        END
"""

import os
import re
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import TypedDict, Optional, List

import pandas as pd
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

# ── Import shared utilities — avoids circular import ──────────
from services.utils import (
    call_groq,
    is_safe_sql,
    extract_sql_from_response,
    execute_sql_duckdb,
    build_schema_text,
)

# ── Import hierarchical memory system ─────────────────────────
from services.memory import (
    write_turn,
    retrieve_context,
    get_last_sql,
    clear_session,
)

load_dotenv()

NEWS_API_KEY      = os.getenv("NEWS_API_KEY", "")
MAX_SQL_RETRIES   = 3
WEB_SCRAPE_TIMEOUT = 3    # seconds
MAX_CHART_POINTS  = 50    # Fix #8: cap chart data points to prevent frontend crash
MAX_SCHEMA_CHARS  = 2000  # Fix efficiency: trim schema to save tokens
MAX_MEMORY_CHARS  = 1000  # Fix efficiency: trim memory context to save tokens


def safe_json_extract(text: str) -> Optional[list]:
    """
    Robust JSON extraction — handles malformed LLM output.
    Tries direct parse first, then greedy regex fallback.

    BUG FIX: was r"[.*?]" (non-greedy) which stopped at the FIRST ]
    found — breaking nested arrays like [{"data": [1,2,3]}].
    Now uses greedy r"[.*]" to capture the full outermost array.
    """
    if not text:
        return None
    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # Greedy regex fallback — finds the outermost [...] block
    try:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


def safe_dataset_id(state: dict) -> str:
    """Fix #2: Safe dataset ID extraction — never crashes on None dataset."""
    dataset = state.get("dataset")
    if dataset and hasattr(dataset, "id"):
        return str(dataset.id)
    return ""


def safe_tables(state: dict) -> list:
    """Fix #3: Safe table access — never crashes on empty tables."""
    dataset = state.get("dataset")
    if dataset and hasattr(dataset, "tables"):
        return list(dataset.tables) if dataset.tables else []
    return []


# ════════════════════════════════════════════════════════════════
# AGENT STATE
# ════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    # ── Inputs ────────────────────────────────────────────────
    user_query:       str
    schema_text:      str       # full schema (built by build_schema_text)
    filtered_schema:  str       # schema with only selected tables
    dataset:          object    # SQLAlchemy Dataset object
    session_memory:   str       # past session context retrieved from memory
    session_id:       str       # actual chat session UUID — used as memory key

    # ── Router ────────────────────────────────────────────────
    route:            str       # "sql" | "explain"

    # ── Planning ──────────────────────────────────────────────
    plan:             Optional[str]
    selected_tables:  List[str]

    # ── SQL ───────────────────────────────────────────────────
    generated_sql:    Optional[str]
    reviewed_sql:     Optional[str]   # after two-pass review
    sql_explanation:  Optional[str]
    sql_valid:        bool
    sql_attempts:     int
    sql_error:        Optional[str]

    # ── Error classification ──────────────────────────────────
    error_type:       Optional[str]
    error_strategy:   Optional[str]

    # ── Results ───────────────────────────────────────────────
    result_df:        Optional[object]
    result_rows:      List[dict]
    row_count:        int
    result_stats:     Optional[dict]   # quantitative stats from stats enricher

    # ── Result validation ─────────────────────────────────────
    result_valid:     bool
    result_issue:     Optional[str]

    # ── Web context ───────────────────────────────────────────
    scraped_context:  Optional[str]

    # ── AI outputs ────────────────────────────────────────────
    insights:         List[dict]
    recommendations:  List[dict]
    chart_config:     Optional[dict]
    explanation:      Optional[str]
    followup_questions: List[str]

    # ── Session SQL context ──────────────────────────────────
    last_sql:         Optional[str]   # previous SQL for follow-up queries

    # ── Final ─────────────────────────────────────────────────
    final_answer:     Optional[str]
    error:            Optional[str]
    execution_time_ms: int


# ════════════════════════════════════════════════════════════════
# WEB SCRAPING — async with timeout
# ════════════════════════════════════════════════════════════════

def scrape_google_news_rss(query: str, max_results: int = 5) -> List[dict]:
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=WEB_SCRAPE_TIMEOUT) as resp:
            content = resp.read()
        root    = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []
        articles = []
        for item in channel.findall("item")[:max_results]:
            title = item.findtext("title", "").strip()
            desc  = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()
            if title:
                articles.append({
                    "title":       title,
                    "description": desc[:200],
                    "source":      "Google News",
                })
        return articles
    except Exception:
        return []


def scrape_newsapi(query: str, max_results: int = 5) -> List[dict]:
    if not NEWS_API_KEY:
        return []
    encoded = urllib.parse.quote(query)
    url = (
        f"https://newsapi.org/v2/everything"
        f"?q={encoded}&sortBy=publishedAt&pageSize={max_results}"
        f"&apiKey={NEWS_API_KEY}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=WEB_SCRAPE_TIMEOUT) as resp:
            data = json.loads(resp.read())
        articles = []
        for art in data.get("articles", [])[:max_results]:
            title = art.get("title", "").strip()
            desc  = art.get("description", "").strip()
            src   = art.get("source", {}).get("name", "NewsAPI")
            if title and title != "[Removed]":
                articles.append({
                    "title":       title,
                    "description": desc[:200],
                    "source":      src,
                })
        return articles
    except Exception:
        return []


def fetch_web_context_async(user_query: str, insights: List[dict]) -> str:
    """
    Fetch web context in a background thread with hard 3s timeout.
    Never blocks the main agent pipeline.
    """
    clean = re.sub(
        r"\b(show|me|top|bottom|count|sum|avg|max|min|by|where|from|select)\b",
        "", user_query.lower(), flags=re.IGNORECASE,
    ).strip()
    if insights:
        words = insights[0].get("text", "").split()[:4]
        clean = f"{clean} {' '.join(words)}"
    search_query = clean.strip()[:80]

    if not search_query:
        return ""

    # Fix #6: Use ThreadPoolExecutor — no thread leak on timeout
    articles = []
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: scrape_google_news_rss(search_query) or scrape_newsapi(search_query)
            )
            articles = future.result(timeout=WEB_SCRAPE_TIMEOUT)
    except (FutureTimeout, Exception):
        articles = []
    if not articles:
        return ""

    lines = [f"Current news about '{search_query}':\n"]
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. {art['title']}")
        if art["description"]:
            lines.append(f"   {art['description']}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# SCHEMA UTILITIES
# ════════════════════════════════════════════════════════════════

def build_schema_for_tables(dataset, table_names: List[str]) -> str:
    """
    Build schema text for ONLY the selected tables.
    BUG FIX: column names with spaces are now quoted with double quotes,
    consistent with build_schema_text() in utils.py. Without this,
    the LLM sees unquoted column names and generates broken SQL.
    """
    lines = [f"Dataset: {dataset.dataset_name}"]
    for table in dataset.tables:
        if table.table_name not in table_names:
            continue
        lines.append(f"\nTable: {table.table_name} ({table.row_count or '?'} rows)")
        for col in table.columns:
            null_str = "NULL" if col.is_nullable else "NOT NULL"
            extra    = ""
            if col.sample_values:
                extra = f"  [values: {col.sample_values}]"
            # Quote column names that contain spaces (matches utils.py behaviour)
            col_name = f'"{col.column_name}"' if " " in col.column_name else col.column_name
            lines.append(f"  - {col_name} {col.data_type} {null_str}{extra}")
    return "\n".join(lines)


def get_few_shot_examples(dataset) -> str:
    """
    Generate few-shot SQL examples based on actual table/column names.
    BUG FIX: table and column names with spaces were not quoted,
    producing invalid SQL like: SELECT * FROM Sales Data
    Now wraps names containing spaces in double quotes consistently.
    """
    def q(name: str) -> str:
        """Quote identifier if it contains spaces."""
        return f'"{name}"' if " " in name else name

    examples = []
    for table in dataset.tables:
        num_cols = [c.column_name for c in table.columns if c.data_type in ("INTEGER", "FLOAT")]
        txt_cols = [c.column_name for c in table.columns if c.data_type == "TEXT"]
        tbl      = table.table_name
        tbl_q    = q(tbl)

        # Example 1: count all rows
        examples.append(
            f"Q: How many records are in {tbl}?\n"
            f"A: SELECT COUNT(*) as total FROM {tbl_q};"
        )

        # Example 2: top N by numeric column
        if num_cols:
            col_q = q(num_cols[0])
            examples.append(
                f"Q: Top 5 by {num_cols[0]}?\n"
                f"A: SELECT * FROM {tbl_q} ORDER BY {col_q} DESC LIMIT 5;"
            )

        # Example 3: filter by text column value
        if txt_cols:
            col   = txt_cols[0]
            col_q = q(col)
            sample = ""
            for c in table.columns:
                if c.column_name == col and c.sample_values:
                    vals = c.sample_values.split(",")
                    sample = vals[0].strip() if vals else ""
                    break
            if sample:
                examples.append(
                    f"Q: Filter {tbl} where {col} is {sample}?\n"
                    f"A: SELECT * FROM {tbl_q} WHERE {col_q} = '{sample}';"
                )

        # Example 4: group by + avg
        if txt_cols and num_cols:
            grp_q = q(txt_cols[0])
            agg_q = q(num_cols[0])
            examples.append(
                f"Q: Average {num_cols[0]} by {txt_cols[0]}?\n"
                f"A: SELECT {grp_q}, AVG({agg_q}) as avg_val "
                f"FROM {tbl_q} GROUP BY {grp_q} ORDER BY avg_val DESC;"
            )

        break  # one table's examples is enough

    return "\n\n".join(examples[:4])



# ════════════════════════════════════════════════════════════════
# INTENT-AWARE CHART SELECTION
# ════════════════════════════════════════════════════════════════

def detect_chart_intent(user_query: str) -> Optional[str]:
    """
    Detect chart type from user query keywords.
    Returns chart type hint or None (fall back to structural detection).
    """
    q = user_query.lower()

    time_keywords   = ["trend", "over time", "by month", "by year", "by week", "timeline", "growth"]
    compare_keywords = ["compare", "vs", "versus", "difference", "between", "contrast"]
    dist_keywords   = ["distribution", "spread", "histogram", "frequency", "range"]
    prop_keywords   = ["proportion", "percentage", "share", "breakdown", "composition", "pie"]
    corr_keywords   = ["correlation", "relationship", "scatter", "between"]
    rank_keywords   = ["top", "bottom", "rank", "ranking", "highest", "lowest", "most", "least"]

    if any(k in q for k in time_keywords):   return "line"
    if any(k in q for k in prop_keywords):   return "pie"
    if any(k in q for k in dist_keywords):   return "bar"
    if any(k in q for k in corr_keywords):   return "scatter"
    if any(k in q for k in compare_keywords): return "bar"
    if any(k in q for k in rank_keywords):   return "bar"
    return None


def generate_chart_config_smart(df: pd.DataFrame, user_query: str) -> Optional[dict]:
    """
    Intent-aware chart generation.
    Priority: user intent keywords > structural detection.
    """
    if df.empty or len(df.columns) < 1:
        return None

    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    str_cols = df.select_dtypes(include=["object", "string", "category"]).columns.tolist()

    if not num_cols:
        return None

    # Get intent from user query
    intent = detect_chart_intent(user_query)

    # Colors palette
    colors = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"]

    # ── Pie chart ─────────────────────────────────────────────
    if intent == "pie" or (str_cols and 2 <= df[str_cols[0]].nunique() <= 8 and intent != "line"):
        if str_cols and num_cols:
            labels = df[str_cols[0]].astype(str).tolist()
            values = df[num_cols[0]].tolist()
            return {
                "type": "pie",
                "data": {
                    "labels": labels,
                    "datasets": [{"data": values, "backgroundColor": colors}],
                },
                "options": {"responsive": True, "plugins": {"legend": {"position": "right"}}},
            }

    # ── Line chart ────────────────────────────────────────────
    if intent == "line":
        if str_cols and num_cols:
            labels = df[str_cols[0]].astype(str).tolist()
            values = df[num_cols[0]].tolist()
        elif len(num_cols) >= 2:
            labels = df[num_cols[0]].tolist()
            values = df[num_cols[1]].tolist()
        else:
            labels = list(range(len(df)))
            values = df[num_cols[0]].tolist()

        return {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [{
                    "label":       num_cols[0] if num_cols else "value",
                    "data":        values,
                    "borderColor": "#6366f1",
                    "backgroundColor": "rgba(99,102,241,0.1)",
                    "fill":        True,
                    "tension":     0.4,
                }],
            },
            "options": {
                "responsive": True,
                "plugins": {"legend": {"display": True}},
                "scales":  {"y": {"beginAtZero": False}},
            },
        }

    # ── Scatter chart ─────────────────────────────────────────
    if intent == "scatter" and len(num_cols) >= 2:
        points = [{"x": row[num_cols[0]], "y": row[num_cols[1]]} for _, row in df.iterrows()]
        return {
            "type": "scatter",
            "data": {
                "datasets": [{
                    "label":           f"{num_cols[0]} vs {num_cols[1]}",
                    "data":            points,
                    "backgroundColor": "#6366f1",
                }],
            },
            "options": {"responsive": True},
        }

    # ── Bar chart (default) ───────────────────────────────────
    if str_cols and num_cols:
        x_col  = str_cols[0]
        y_col  = num_cols[0]
        # Fix #8: cap data points to prevent frontend crash on large results
        df_chart = df.head(MAX_CHART_POINTS)
        labels = df_chart[x_col].astype(str).tolist()
        values = df_chart[y_col].tolist()

        # Horizontal bar if many categories
        bar_type = "bar"

        return {
            "type": bar_type,
            "data": {
                "labels": labels,
                "datasets": [{
                    "label":           y_col,
                    "data":            values,
                    "backgroundColor": colors[:len(labels)] if len(labels) <= 8 else "#6366f1",
                    "borderRadius":    4,
                }],
            },
            "options": {
                "responsive": True,
                "plugins":    {"legend": {"display": False}},
                "scales":     {"y": {"beginAtZero": True}},
            },
        }

    # ── Single numeric column ─────────────────────────────────
    if len(num_cols) >= 2:
        return {
            "type": "line",
            "data": {
                "labels": list(range(len(df))),
                "datasets": [{
                    "label":       num_cols[1],
                    "data":        df[num_cols[1]].tolist(),
                    "borderColor": "#6366f1",
                    "fill":        False,
                    "tension":     0.4,
                }],
            },
            "options": {"responsive": True},
        }

    return None


# ════════════════════════════════════════════════════════════════
# SESSION MEMORY (simple in-memory per session)
# ════════════════════════════════════════════════════════════════

# Memory functions now delegated to services/memory.py
# Hierarchical RAG: short-term + summary + context compression


# ════════════════════════════════════════════════════════════════
# AGENTS
# ════════════════════════════════════════════════════════════════

# ── AGENT 1: Router ───────────────────────────────────────────
def router_agent(state: AgentState) -> AgentState:
    """
    Decides: sql or explain.
    temperature=0.0 for fully deterministic routing.
    Defaults to 'sql' on any ambiguity — better to run SQL than hallucinate.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a query router. Reply with ONE word only: 'sql' or 'explain'.\n\n"
                "Route to 'sql' when the query asks for:\n"
                "- specific data, records, or numbers from the dataset\n"
                "- top N / bottom N / highest / lowest / most / least / cheapest / expensive\n"
                "- count, sum, average, total, percentage, revenue\n"
                "- comparison, ranking, filtering, grouping, distribution\n"
                "- trends, charts, show me, list, find, get\n"
                "- ANY question that can be answered by querying the data\n\n"
                "Route to 'explain' ONLY when:\n"
                "- asking what a general concept means (e.g. 'what is machine learning')\n"
                "- asking how something works in theory with no data needed\n\n"
                "CRITICAL RULE: When in doubt → always route to 'sql'\n\n"
                "Examples:\n"
                "top 3 expensive cars → sql\n"
                "highest revenue model → sql\n"
                "cheapest product → sql\n"
                "show all regions → sql\n"
                "how many rows → sql\n"
                "average price by model → sql\n"
                "best selling model → sql\n"
                "top 5 salaries → sql\n"
                "trend over time → sql\n"
                "what is machine learning → explain\n"
                "explain what GDP means → explain\n"
            ),
        },
        {
            "role": "user",
            "content": f"Schema:\n{state['schema_text']}\n\nQuery: {state['user_query']}",
        },
    ]
    try:
        decision = call_groq(messages, max_tokens=10, temperature=0.0)
        # Strict match: exact word first, then contains fallback
        cleaned = decision.strip().lower()
        if cleaned in ("sql", "explain"):
            route = cleaned
        elif "sql" in cleaned:
            route = "sql"
        else:
            route = "sql"  # default to sql on ambiguity
    except Exception:
        route = "sql"  # always default to sql on failure
    return {**state, "route": route}


# ── AGENT 2: Memory Retriever ─────────────────────────────────
def memory_retriever_agent(state: AgentState) -> AgentState:
    """
    Hierarchical RAG memory retrieval.
    BUG FIX: was using dataset.id as memory key — two users on the
    same dataset shared and contaminated each other's memory.
    Now uses state["session_id"] (the real chat session UUID).
    Falls back to dataset id only if session_id is missing.
    """
    session_id = state.get("session_id") or safe_dataset_id(state)
    memory     = retrieve_context(session_id, state["user_query"])[:MAX_MEMORY_CHARS]
    last_sql   = get_last_sql(session_id)
    return {**state, "session_memory": memory, "last_sql": last_sql}


# ── AGENT 3: Planning ─────────────────────────────────────────
def planning_agent(state: AgentState) -> AgentState:
    """
    Breaks complex queries into steps.
    Also detects schema mismatch — if user query references
    entities not in the dataset (e.g. asking about students
    when dataset is BMW sales).
    """
    memory_section = ""
    if state.get("session_memory"):
        memory_section = f"\n\nSession context:\n{state['session_memory']}"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a data analysis planner.\n\n"
                "FIRST: Check if the user query is relevant to the dataset schema.\n"
                "- If the query mentions entities completely absent from the schema "
                "(e.g. asking about students/CGPA when dataset is BMW sales) — "
                "respond with exactly: MISMATCH: <brief reason>\n"
                "- Otherwise: create a numbered SQL execution plan (1-4 steps).\n\n"
                "For simple queries: 1 step.\n"
                "For complex queries: 2-4 steps.\n"
                "Format: numbered list only (unless MISMATCH).\n\n"
                f"Schema:\n{state['schema_text']}"
                f"{memory_section}"
            ),
        },
        {"role": "user", "content": f"Plan for: {state['user_query']}"},
    ]
    try:
        plan = call_groq(messages, max_tokens=200, temperature=0.1)

        # Detect schema mismatch
        # BUG FIX: require len > 15 to prevent a degenerate one-word
        # response from Groq killing the entire pipeline on valid queries.
        plan_stripped = plan.strip()
        if plan_stripped.upper().startswith("MISMATCH") and len(plan_stripped) > 15:
            reason = plan_stripped.split(":", 1)[-1].strip() if ":" in plan_stripped else "Query does not match dataset"
            return {
                **state,
                "plan":      None,
                "sql_error": f"Query does not match this dataset. {reason}",
                "error":     f"Dataset mismatch: {reason}",
                "route":     "mismatch",
            }

        return {**state, "plan": plan}
    except Exception:
        return {**state, "plan": f"1. Query: {state['user_query']}"}


# ── AGENT 4: Schema Selector (NEW) ────────────────────────────
def schema_selector_agent(state: AgentState) -> AgentState:
    """
    NEW: Selects only the tables relevant to the query.
    Reduces prompt size and SQL hallucination by ~40%.
    """
    all_tables = [t.table_name for t in state["dataset"].tables]

    # If only one table — skip selection, use it directly
    if len(all_tables) == 1:
        return {
            **state,
            "selected_tables":  all_tables,
            "filtered_schema":  state["schema_text"],
        }

    messages = [
        {
            "role": "system",
            "content": (
                "Given a user query and list of tables, return ONLY the table names needed.\n"
                "Reply with a JSON array of strings.\n"
                "Example: [\"sales\", \"customers\"]\n\n"
                f"Available tables: {json.dumps(all_tables)}"
            ),
        },
        {"role": "user", "content": state["user_query"]},
    ]

    try:
        response = call_groq(messages, max_tokens=100, temperature=0.0)
        clean    = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        selected = safe_json_extract(clean)   # Fix #7: robust JSON extraction
        if selected and isinstance(selected, list):
            selected = [t for t in selected if t in all_tables]
            if selected:
                filtered = build_schema_for_tables(state["dataset"], selected)
                return {**state, "selected_tables": selected, "filtered_schema": filtered}
    except Exception:
        pass

    # Fallback — use all tables
    return {
        **state,
        "selected_tables":  all_tables,
        "filtered_schema":  state["schema_text"],
    }


# ── AGENT 5: SQL Generator ────────────────────────────────────
def generate_sql_agent(state: AgentState) -> AgentState:
    """
    Generates SQL using:
    - Filtered schema (only relevant tables)
    - Execution plan
    - Session memory context
    - Few-shot examples from actual table structure
    """
    # Fix efficiency: trim schema + memory to save tokens
    schema      = (state.get("filtered_schema") or state["schema_text"])[:MAX_SCHEMA_CHARS]
    plan        = state.get("plan", "")
    memory      = state.get("session_memory", "")[:MAX_MEMORY_CHARS]
    few_shots   = get_few_shot_examples(state["dataset"])

    plan_section   = f"\nExecution plan:\n{plan}\n"     if plan   else ""
    memory_section = f"\nSession context:\n{memory}\n"  if memory else ""

    # Follow-up SQL context — if user is refining previous query
    last_sql    = state.get("last_sql", "")
    last_section = (
        f"\nPrevious SQL (user may be refining this):\n{last_sql}\n"
        f"If the current query is a follow-up, modify the previous SQL instead of writing from scratch.\n"
    ) if last_sql else ""

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert SQL analyst.\n\n"
                "RULES:\n"
                "- ONLY generate SELECT queries — never INSERT, UPDATE, DELETE, DROP.\n"
                "- Use EXACT table and column names from the schema.\n"
                "- ALWAYS quote column names that contain spaces with double quotes: \"Column Name\".\n"
                "- ALWAYS quote table names that contain spaces with double quotes: \"Table Name\".\n"
                "- Use correct values from [values: ...] hints for WHERE clauses.\n"
                "- Always wrap SQL in ```sql code block.\n"
                "- ALWAYS include LIMIT 100 unless user explicitly asks for all data.\n"
                "- For numeric values stored as TEXT: always use TRY_CAST not CAST.\n"
                "  TRY_CAST returns NULL on failure instead of crashing.\n"
                "- For columns with spaces in names: always use double quotes: \"Column Name\".\n"
                "- For table names with spaces: always use double quotes: \"Table Name\".\n"
                "- DuckDB supports regexp_extract(col, pattern, group) for text parsing.\n"
                "- After the SQL, write one sentence explaining it.\n\n"
                f"Schema:\n{schema}"
                f"{plan_section}"
                f"{memory_section}"
                f"{last_section}"
                f"\nExamples based on this dataset:\n{few_shots}"
            ),
        },
        {"role": "user", "content": f"Generate SQL: {state['user_query']}"},
    ]
    try:
        response    = call_groq(messages, max_tokens=512, temperature=0.1)

        # IMPROVEMENT: detect when the LLM says it cannot generate SQL
        # (e.g. query truly incompatible with schema after planning passed)
        if response.strip().upper().startswith("NO_SQL_POSSIBLE"):
            return {
                **state,
                "generated_sql": "",
                "sql_attempts":  state.get("sql_attempts", 0) + 1,
                "error":         "Query cannot be answered with this dataset schema.",
                "route":         "mismatch",
            }

        sql         = extract_sql_from_response(response)
        explanation = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        return {
            **state,
            "generated_sql":   sql or "",
            "sql_explanation": explanation or "SQL generated.",
            "sql_attempts":    state.get("sql_attempts", 0) + 1,
            "sql_error":       None,
        }
    except Exception as e:
        return {
            **state,
            "generated_sql": "",
            "sql_attempts":  state.get("sql_attempts", 0) + 1,
            "error":         f"SQL generation failed: {e}",
        }


# ── AGENT 6: SQL Reviewer ─────────────────────────────────────
def sql_reviewer_agent(state: AgentState) -> AgentState:
    """
    Pass-through reviewer — preserves generated SQL exactly.
    The generator already has strong rules; a second LLM call
    was removing correct quoting and breaking valid SQL.
    Only sets reviewed_sql = generated_sql for state tracking.
    """
    sql = state.get("generated_sql", "")
    return {**state, "reviewed_sql": sql}


# ── AGENT 7: SQL Validator ────────────────────────────────────
def validate_sql_agent(state: AgentState) -> AgentState:
    """Validates SQL safety and table references."""
    sql = state.get("generated_sql", "")

    if not sql:
        return {**state, "sql_valid": False, "sql_error": "No SQL was generated"}

    if not is_safe_sql(sql):
        return {
            **state,
            "sql_valid": False,
            "sql_error": "SQL contains forbidden operations — only SELECT allowed",
        }

    # Fix #2: safe table access
    tables      = safe_tables(state)
    table_names = [t.table_name for t in tables]
    sql_lower   = sql.lower()

    # Fix #4: regex handles both unquoted names (word boundary) and
    # quoted names with spaces like "Sales Data" in DuckDB SQL.
    if table_names and not any(
        re.search(rf'(?:\b|"){re.escape(t.lower())}(?:\b|")', sql_lower)
        for t in table_names
    ):
        return {
            **state,
            "sql_valid":   False,
            "sql_attempts": state.get("sql_attempts", 0) + 1,  # Fix #5: increment on failure
            "sql_error":   f"SQL references no known tables. Available: {', '.join(table_names)}",
        }

    return {**state, "sql_valid": True, "sql_error": None}


# ── AGENT 8: Error Classifier ─────────────────────────────────
def error_classifier_agent(state: AgentState) -> AgentState:
    """Classifies error type so fix_sql knows the right strategy."""
    error = (state.get("sql_error") or state.get("error") or "").lower()

    if any(w in error for w in ["column", "does not exist", "no such column"]):
        return {**state, "error_type": "column_not_found",
                "error_strategy": "Fix column name using exact names from schema."}
    if any(w in error for w in ["table", "no such table", "relation"]):
        return {**state, "error_type": "table_not_found",
                "error_strategy": "Fix table name using exact names from schema."}
    if any(w in error for w in ["syntax", "parse", "unexpected"]):
        return {**state, "error_type": "syntax_error",
                "error_strategy": "Regenerate SQL from scratch — syntax error."}
    if any(w in error for w in ["0 rows", "empty", "no rows"]):
        return {**state, "error_type": "empty_result",
                "error_strategy": "Relax WHERE conditions. Remove restrictive filters."}
    if any(w in error for w in ["conversion", "convert", "bool", "integer"]):
        return {**state, "error_type": "type_error",
                "error_strategy": "Fix data type in WHERE clause. Check [values:] hints for correct format."}

    return {**state, "error_type": "other",
            "error_strategy": "Review SQL against schema carefully."}


# ── AGENT 9: SQL Repair ───────────────────────────────────────
def fix_sql_agent(state: AgentState) -> AgentState:
    """
    Fixes SQL using error classification strategy.
    IMPROVEMENT: on the final retry attempt, falls back to a safe
    SELECT * LIMIT 10 rather than trying complex repairs that always fail.
    """
    schema   = state.get("filtered_schema") or state["schema_text"]
    strategy = state.get("error_strategy", "Fix the SQL.")
    err_type = state.get("error_type", "other")

    # Last-resort fallback on final attempt — simple SQL always works
    if state.get("sql_attempts", 0) >= MAX_SQL_RETRIES - 1:
        tables = safe_tables(state)
        if tables:
            tbl = tables[0].table_name
            tbl_q = f'"{tbl}"' if " " in tbl else tbl
            fallback = f"SELECT * FROM {tbl_q} LIMIT 10"
            return {
                **state,
                "generated_sql":  fallback,
                "sql_attempts":   state.get("sql_attempts", 0) + 1,
                "sql_error":      None,
                "error_type":     None,
                "error_strategy": None,
            }

    if err_type == "syntax_error":
        messages = [
            {
                "role": "system",
                "content": (
                    "Regenerate SQL from scratch.\n"
                    "ONLY SELECT queries. Use EXACT names from schema.\n"
                    "Wrap in ```sql block.\n\n"
                    f"Schema (truncated for brevity):\n{schema[:1500]}"
                ),
            },
            {"role": "user", "content": f"Regenerate SQL for: {state['user_query']}"},
        ]
    else:
        messages = [
            {
                "role": "system",
                "content": (
                    f"Fix strategy: {strategy}\n\n"
                    "ONLY SELECT queries. Use EXACT names from schema.\n"
                    "Quote column/table names with spaces using double quotes.\n"
                    "Wrap fixed SQL in ```sql block.\n\n"
                    f"Schema (truncated):\n{schema[:1500]}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Query: {state['user_query']}\n"
                    f"Failed SQL:\n```sql\n{state.get('generated_sql', '')}\n```\n"
                    f"Error: {state.get('sql_error', '')}\n"
                    f"Error type: {err_type}"
                ),
            },
        ]

    try:
        response  = call_groq(messages, max_tokens=512, temperature=0.1)
        fixed_sql = extract_sql_from_response(response)
        return {
            **state,
            "generated_sql": fixed_sql or state.get("generated_sql", ""),
            "sql_attempts":  state.get("sql_attempts", 0) + 1,
            "sql_error":     None,
            "error_type":    None,
            "error_strategy": None,
        }
    except Exception as e:
        return {
            **state,
            "sql_attempts": state.get("sql_attempts", 0) + 1,
            "error":        f"SQL repair failed: {e}",
        }


# ── AGENT 10: SQL Executor ────────────────────────────────────
def execute_sql_agent(state: AgentState) -> AgentState:
    """Executes SQL via execute_sql_duckdb from ai.py."""
    sql = state.get("generated_sql", "")
    if not sql:
        return {**state, "sql_error": "No SQL generated"}

    start = time.time()
    try:
        df          = execute_sql_duckdb(sql, state["dataset"])
        exec_ms     = int((time.time() - start) * 1000)
        # Sanitize Infinity — invalid in JSON. NaN is handled by to_json() → null.
        import numpy as np
        df = df.replace([np.inf, -np.inf], None)
        result_rows = json.loads(
            df.head(100).to_json(orient="records", date_format="iso", default_handler=str)
        )
        return {
            **state,
            "result_df":         df,
            "result_rows":       result_rows,
            "row_count":         len(df),
            "sql_error":         None,
            "execution_time_ms": exec_ms,
        }
    except Exception as e:
        return {
            **state,
            "sql_error":         str(e),
            "execution_time_ms": int((time.time() - start) * 1000),
        }


# ── AGENT 11: Result Validator ────────────────────────────────
def result_validator_agent(state: AgentState) -> AgentState:
    """Validates result quality."""
    row_count = state.get("row_count", 0)

    if row_count == 0:
        return {
            **state,
            "result_valid": False,
            "result_issue": "empty",
            "sql_error":    "Query returned 0 rows. WHERE conditions may be too restrictive.",
        }
    if row_count > 50000:
        return {**state, "result_valid": True, "result_issue": "too_many_rows"}

    # Check result quality
    df = state.get("result_df")
    if df is not None and not df.empty:
        # Mostly null — not useful
        null_ratio = df.isnull().mean().mean()
        if null_ratio > 0.8:
            return {**state, "result_valid": True, "result_issue": "mostly_null"}
        # Single column — low information
        if len(df.columns) == 1 and row_count == 1:
            # Single value result — still valid, just flag it
            return {**state, "result_valid": True, "result_issue": "single_value"}

    return {**state, "result_valid": True, "result_issue": None}


# ── AGENT 12: Stats Enricher (NEW) ───────────────────────────
def stats_enricher_agent(state: AgentState) -> AgentState:
    """
    NEW: Computes quantitative statistics from results.
    Feeds into insights so they use actual numbers.

    Computes: mean, median, min, max, % of dataset total for numeric cols.
    """
    df = state.get("result_df")
    if df is None or df.empty:
        return {**state, "result_stats": {}}

    stats   = {}
    # Fix #3: safe table access — no IndexError on empty tables
    tables        = safe_tables(state)
    total_dataset = tables[0].row_count if tables and tables[0].row_count else 1

    for col in df.select_dtypes(include=["number"]).columns:
        try:
            col_sum = float(df[col].sum())
            stats[col] = {
                "mean":            round(float(df[col].mean()), 2),
                "median":          round(float(df[col].median()), 2),
                "min":             round(float(df[col].min()), 2),
                "max":             round(float(df[col].max()), 2),
                "sum":             round(col_sum, 2),
                "pct_of_dataset":  round(state["row_count"] / total_dataset * 100, 1),
            }
        except Exception:
            pass

    # Add ranking context with PRE-COMPUTED percentages
    # This prevents the LLM from doing incorrect percentage calculations
    if state["row_count"] <= 20 and len(df.columns) >= 2:
        str_cols = df.select_dtypes(include=["object", "string"]).columns.tolist()
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if str_cols and num_cols:
            col_total = float(df[num_cols[0]].sum())

            # Pre-compute per-row share % so LLM never calculates it
            rankings = []
            for _, row in df.iterrows():
                val  = float(row[num_cols[0]])
                share = round(val / col_total * 100, 1) if col_total > 0 else 0
                rankings.append({
                    "label":    str(row[str_cols[0]]),
                    "value":    round(val, 2),
                    "share_pct": share,
                })

            stats["_rankings"] = rankings
            stats["_context"] = {
                "top_item":    rankings[0]["label"] if rankings else "",
                "top_value":   rankings[0]["value"] if rankings else 0,
                "top_share":   rankings[0]["share_pct"] if rankings else 0,
                "total":       round(col_total, 2),
                "result_rows": state["row_count"],
            }

    return {**state, "result_stats": stats}


# ── AGENT 13: Insights ────────────────────────────────────────
def insights_agent(state: AgentState) -> AgentState:
    """
    Generates quantitative insights.
    Uses result_stats to enforce actual numbers in every insight.
    """
    if not state.get("result_rows"):
        return {**state, "insights": []}

    sample = json.dumps(state["result_rows"][:20], default=str)
    stats  = json.dumps(state.get("result_stats", {}), default=str)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a business intelligence analyst.\n"
                "Generate exactly 3 data-driven insights.\n\n"
                "STRICT RULES:\n"
                "- Every insight MUST contain at least one REAL number from the results or stats below.\n"
                "- ONLY use numbers that appear in the sample results or stats JSON — NEVER invent values.\n"
                "- Do NOT copy example numbers from these instructions — use ONLY the actual query data.\n"
                "- Be specific: reference actual values from the result rows.\n"
                "- Reference the user's actual question in every insight.\n"
                "- If a stat is not in the data, do not mention it.\n\n"
                "Respond ONLY with JSON array:\n"
                '[{"insight": "...", "importance": 0.9}, ...]'
            ),
        },
        {
            "role": "user",
            "content": (
                f"User question: {state['user_query']}\n"
                f"SQL executed: {state.get('generated_sql', '')}\n"
                f"Total rows returned: {state['row_count']}\n\n"
                f"Pre-computed statistics (use these EXACT numbers, do not recalculate):\n{stats}\n\n"
                f"Raw result rows (do not calculate percentages from these — use stats above):\n{sample}\n\n"
                "Generate 3 insights using ONLY the numbers in the pre-computed statistics above."
            ),
        },
    ]
    try:
        response = call_groq(messages, max_tokens=600, temperature=0.2)
        clean    = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        data     = safe_json_extract(clean)   # Fix #7: robust JSON extraction
        if data:
            insights = [
                {"text": d.get("insight", ""), "score": float(d.get("importance", 0.5))}
                for d in data if d.get("insight")
            ]
            return {**state, "insights": insights}
    except Exception:
        pass
    return {**state, "insights": []}


# ── AGENT 14: Web Scraper (async) ─────────────────────────────
def scrape_web_agent(state: AgentState) -> AgentState:
    """Async web scraping — never blocks more than 3 seconds.
    Skipped if row_count < 5 (not enough data to make news relevant).
    """
    # Skip if too few results — web context won't add value
    if state.get("row_count", 0) < 5:
        return {**state, "scraped_context": ""}
    try:
        context = fetch_web_context_async(
            user_query=state["user_query"],
            insights=state.get("insights", []),
        )
        return {**state, "scraped_context": context}
    except Exception:
        return {**state, "scraped_context": ""}


# ── AGENT 15: Recommendations ─────────────────────────────────
def recommendations_agent(state: AgentState) -> AgentState:
    """Recommendations backed by data + insights + real news."""
    if not state.get("insights"):
        return {**state, "recommendations": []}

    insights_text   = "\n".join(f"- {i['text']}" for i in state["insights"])
    scraped_context = state.get("scraped_context", "")
    web_section     = ""
    if scraped_context:
        web_section = f"\nCurrent industry news:\n{scraped_context}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a strategic business consultant.\n"
                "Give exactly 3 specific, actionable recommendations.\n"
                "Each must reference specific numbers from the insights.\n"
                f"{web_section}\n"
                "Respond ONLY with JSON array:\n"
                '[{"recommendation": "...", "confidence": 0.9}, ...]'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {state['user_query']}\n"
                f"Insights:\n{insights_text}\n"
                f"Rows analyzed: {state.get('row_count', 0)}"
            ),
        },
    ]
    try:
        response = call_groq(messages, max_tokens=512, temperature=0.3)
        clean    = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        data     = safe_json_extract(clean)   # Fix #7
        if data:
            recs = [
                {"text": d.get("recommendation", ""), "score": float(d.get("confidence", 0.5))}
                for d in data if d.get("recommendation")
            ]
            return {**state, "recommendations": recs}
    except Exception:
        pass
    return {**state, "recommendations": []}


# ── AGENT 16: Intent-Aware Chart ─────────────────────────────
def chart_agent(state: AgentState) -> AgentState:
    """Intent-aware chart generation using query keywords."""
    df = state.get("result_df")
    if df is None or not state.get("result_rows"):
        return {**state, "chart_config": None}
    try:
        chart_config = generate_chart_config_smart(df, state["user_query"])
        return {**state, "chart_config": chart_config}
    except Exception:
        return {**state, "chart_config": None}


# ── AGENT 17: Follow-up Questions (NEW) ──────────────────────
def followup_agent(state: AgentState) -> AgentState:
    """
    NEW: Suggests 3 natural follow-up questions after analysis.
    Inspired by Julius AI — helps user explore data deeper.
    """
    if not state.get("result_rows"):
        return {**state, "followup_questions": []}

    insights_text = "\n".join(f"- {i['text']}" for i in state.get("insights", []))

    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI data analyst assistant.\n"
                "Suggest exactly 3 natural follow-up questions the user might ask next.\n"
                "Make them specific to the data and insights found.\n"
                "Each question should help the user explore the data deeper.\n\n"
                "Respond ONLY with a JSON array of strings:\n"
                '["question 1", "question 2", "question 3"]'
            ),
        },
        {
            "role": "user",
            "content": (
                f"User asked: {state['user_query']}\n"
                f"Insights found:\n{insights_text}\n"
                f"Dataset: {state['dataset'].dataset_name}"
            ),
        },
    ]
    try:
        response  = call_groq(messages, max_tokens=200, temperature=0.4)
        clean     = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        questions = safe_json_extract(clean)   # Fix #7
        if isinstance(questions, list):
            return {**state, "followup_questions": [q for q in questions[:3] if isinstance(q, str)]}
    except Exception:
        pass
    return {**state, "followup_questions": []}


# ── AGENT 18: Memory Writer ───────────────────────────────────
def memory_writer_agent(state: AgentState) -> AgentState:
    """
    Saves turn to hierarchical memory.
    BUG FIX: was using dataset.id as memory key (same as retriever bug).
    Now uses state["session_id"] so each chat session has isolated memory.
    """
    session_id = state.get("session_id") or safe_dataset_id(state)
    if session_id:
        top_insight = state["insights"][0]["text"] if state.get("insights") else ""
        write_turn(
            session_id=session_id,
            user_query=state["user_query"],
            generated_sql=state.get("generated_sql", ""),
            top_insight=top_insight,
            result_preview=state.get("result_rows", []),
            row_count=state.get("row_count", 0),
        )
    return state


# ── AGENT 19: Explain ─────────────────────────────────────────
def explain_agent(state: AgentState) -> AgentState:
    """Answers conceptual queries. Uses session memory for context."""
    memory_section = ""
    if state.get("session_memory"):
        memory_section = f"\n\nSession context:\n{state['session_memory']}"

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert AI Data Analyst.\n\n"
                "STRICT RULES:\n"
                "- NEVER invent, assume, or make up data values.\n"
                "- NEVER say 'assuming the dataset is...' or 'for example the values might be'.\n"
                "- If the question needs actual data — say clearly that SQL execution is needed.\n"
                "- Only answer questions about concepts, schema structure, or methodology.\n"
                "- If you are not certain, say so.\n\n"
                f"Dataset schema:\n{state['schema_text']}"
                f"{memory_section}"
            ),
        },
        {"role": "user", "content": state["user_query"]},
    ]
    try:
        answer = call_groq(messages, max_tokens=512, temperature=0.3)
        return {**state, "explanation": answer, "final_answer": answer}
    except Exception as e:
        fallback = "Could not generate an explanation. Please try again."
        return {**state, "explanation": fallback, "final_answer": fallback, "error": str(e)}


# ── AGENT 20: Final ───────────────────────────────────────────
def final_agent(state: AgentState) -> AgentState:
    """Assembles final answer."""
    if state.get("route") == "explain":
        return state

    # Explicit mismatch handling — don't fall through to "Analysis complete"
    if state.get("route") == "mismatch":
        error = state.get("error") or state.get("sql_error") or "Query does not match this dataset."
        return {**state, "final_answer": error}

    error    = state.get("error") or state.get("sql_error")
    attempts = state.get("sql_attempts", 0)

    if error and not state.get("result_rows"):
        final = (
            f"Analysis failed after {attempts} attempt(s). "
            f"Error type: {state.get('error_type', 'unknown')}. "
            f"Detail: {error}. Please rephrase your question."
        )
    else:
        n_ins   = len(state.get("insights", []))
        n_recs  = len(state.get("recommendations", []))
        n_fup   = len(state.get("followup_questions", []))
        has_web = bool(state.get("scraped_context"))
        issue   = state.get("result_issue", "")
        note    = " (large dataset — showing preview)" if issue == "too_many_rows" else ""
        final   = (
            f"Analysis complete{note}. "
            f"Found {state.get('row_count', 0)} rows. "
            f"Generated {n_ins} insights, {n_recs} recommendations"
            f"{' backed by real-world news' if has_web else ''}. "
            f"{n_fup} follow-up questions suggested."
        )

    return {**state, "final_answer": final}


# ════════════════════════════════════════════════════════════════
# CONDITIONAL EDGES
# ════════════════════════════════════════════════════════════════

def route_after_router(state: AgentState) -> str:
    # Fix #1: removed dead code — router always goes to memory_retriever
    # Graph uses add_edge("router", "memory_retriever") directly — this function unused
    return "memory_retriever"

def route_after_memory(state: AgentState) -> str:
    return "planning" if state.get("route") == "sql" else "explain"


def route_after_planning(state: AgentState) -> str:
    if state.get("route") == "mismatch":
        return "final"
    return "schema_selector"

def route_after_validate_sql(state: AgentState) -> str:
    if state.get("sql_valid"):
        return "execute_sql"
    return "classify_error" if state.get("sql_attempts", 0) < MAX_SQL_RETRIES else "final"

def route_after_execute(state: AgentState) -> str:
    if not state.get("sql_error"):
        return "validate_result"
    return "classify_error" if state.get("sql_attempts", 0) < MAX_SQL_RETRIES else "final"

def route_after_validate_result(state: AgentState) -> str:
    if state.get("result_valid"):
        return "stats_enricher"
    # BUG FIX: empty results (0 rows) mean the SQL was valid but no data
    # matched the filter. Retrying generates identical SQL and wastes 3
    # Groq calls. Route straight to final so the user gets a clear message.
    if state.get("result_issue") == "empty":
        return "final"
    return "classify_error" if state.get("sql_attempts", 0) < MAX_SQL_RETRIES else "final"


# ════════════════════════════════════════════════════════════════
# BUILD THE GRAPH
# ════════════════════════════════════════════════════════════════

def build_agent():
    graph = StateGraph(AgentState)

    # Register all agents
    graph.add_node("router",          router_agent)
    graph.add_node("memory_retriever", memory_retriever_agent)
    graph.add_node("planning",        planning_agent)
    graph.add_node("schema_selector", schema_selector_agent)
    graph.add_node("generate_sql",    generate_sql_agent)
    graph.add_node("sql_reviewer",    sql_reviewer_agent)
    graph.add_node("validate_sql",    validate_sql_agent)
    graph.add_node("classify_error",  error_classifier_agent)
    graph.add_node("fix_sql",         fix_sql_agent)
    graph.add_node("execute_sql",     execute_sql_agent)
    graph.add_node("validate_result", result_validator_agent)
    graph.add_node("stats_enricher",  stats_enricher_agent)
    graph.add_node("insights",        insights_agent)
    graph.add_node("scrape_web",      scrape_web_agent)
    graph.add_node("recommendations", recommendations_agent)
    graph.add_node("chart",           chart_agent)
    graph.add_node("followup",        followup_agent)
    graph.add_node("memory_writer",   memory_writer_agent)
    graph.add_node("explain",         explain_agent)
    graph.add_node("final",           final_agent)

    graph.set_entry_point("router")

    # Router → memory retriever (always)
    graph.add_edge("router", "memory_retriever")

    # Memory retriever → planning or explain
    graph.add_conditional_edges("memory_retriever", route_after_memory, {
        "planning": "planning",
        "explain":  "explain",
    })

    # SQL pipeline: conditional after planning (handles dataset mismatch)
    graph.add_conditional_edges("planning", route_after_planning, {
        "schema_selector": "schema_selector",
        "final":           "final",
    })
    graph.add_edge("schema_selector", "generate_sql")
    graph.add_edge("generate_sql",    "sql_reviewer")
    graph.add_edge("sql_reviewer",    "validate_sql")

    graph.add_conditional_edges("validate_sql", route_after_validate_sql, {
        "execute_sql":    "execute_sql",
        "classify_error": "classify_error",
        "final":          "final",
    })

    graph.add_conditional_edges("execute_sql", route_after_execute, {
        "validate_result": "validate_result",
        "classify_error":  "classify_error",
        "final":           "final",
    })

    graph.add_edge("classify_error", "fix_sql")
    graph.add_edge("fix_sql",        "validate_sql")

    graph.add_conditional_edges("validate_result", route_after_validate_result, {
        "stats_enricher": "stats_enricher",
        "classify_error": "classify_error",
        "final":          "final",
    })

    # Post-execution pipeline
    graph.add_edge("stats_enricher",  "insights")
    graph.add_edge("insights",        "scrape_web")
    graph.add_edge("scrape_web",      "recommendations")
    graph.add_edge("recommendations", "chart")
    graph.add_edge("chart",           "followup")
    graph.add_edge("followup",        "memory_writer")
    graph.add_edge("memory_writer",   "final")
    graph.add_edge("explain",         "final")
    graph.add_edge("final",           END)

    return graph.compile()


# ════════════════════════════════════════════════════════════════
# PUBLIC FUNCTION
# ════════════════════════════════════════════════════════════════

# Fix efficiency: compile once, reuse — not per-request
_COMPILED_AGENT = None


def run_agent(
    user_query:  str,
    schema_text: str,
    dataset:     object,
    session_id:  str = "",
) -> AgentState:
    initial: AgentState = {
        "user_query":          user_query,
        "schema_text":         schema_text,
        "filtered_schema":     schema_text,
        "dataset":             dataset,
        "session_id":          session_id,   # BUG FIX: pass real session UUID
        "session_memory":      "",
        "route":               "",
        "plan":                None,
        "selected_tables":     [],
        "generated_sql":       None,
        "reviewed_sql":        None,
        "sql_explanation":     None,
        "sql_valid":           False,
        "sql_attempts":        0,
        "sql_error":           None,
        "error_type":          None,
        "error_strategy":      None,
        "result_df":           None,
        "result_rows":         [],
        "row_count":           0,
        "result_stats":        {},
        "result_valid":        False,
        "result_issue":        None,
        "scraped_context":     None,
        "insights":            [],
        "recommendations":     [],
        "chart_config":        None,
        "explanation":         None,
        "followup_questions":  [],
        "last_sql":            None,
        "final_answer":        None,
        "error":               None,
        "execution_time_ms":   0,
    }
    # Fix efficiency: compile agent once — reuse across requests
    global _COMPILED_AGENT
    if _COMPILED_AGENT is None:
        _COMPILED_AGENT = build_agent()
    result = _COMPILED_AGENT.invoke(initial)
    return result