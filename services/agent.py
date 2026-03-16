"""
services/agent.py  —  LangGraph Multi-Agent AI Analyst

10 specialized agents. Each has ONE responsibility.
All core functions imported from routers/ai.py — zero duplication.

Recommendations backed by real web data (Google News RSS + NewsAPI).

Full agent graph:
─────────────────
query
  │
  ▼
[router_agent]
  ├──► "explain" ──► [explain_agent] ──► [final_agent] ──► END
  │
  └──► "sql"
         │
         ▼
    [planning_agent]        ← decides steps needed for this query
         │
         ▼
    [generate_sql_agent]
         │
         ▼
    [validate_sql_agent]    ← checks SQL before running it
         ├──► invalid ──► [error_classifier_agent] ──► [fix_sql_agent] ──► [generate_sql_agent]
         │
         ▼
    [execute_sql_agent]
         ├──► error ──► [error_classifier_agent] ──► [fix_sql_agent] ──► [execute_sql_agent]
         │              (max 3 attempts total)
         │
         ▼
    [result_validator_agent] ← checks if results make sense
         ├──► empty ──► [fix_sql_agent] (relax query)
         │
         ▼
    [insights_agent]
         │
         ▼
    [scrape_web_agent]       ← fetches real-world news context
         │
         ▼
    [recommendations_agent]  ← backed by data + insights + web news
         │
         ▼
    [chart_agent]
         │
         ▼
    [final_agent]
         │
         ▼
        END
"""

import os
import re
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import TypedDict, Optional, List

import pandas as pd
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

# ── Import all reusable functions from ai.py — no duplication ──
from routers.ai import (
    call_groq,
    is_safe_sql,
    extract_sql_from_response,
    execute_sql_duckdb,
    generate_chart_config,
    build_schema_text,
)

load_dotenv()

NEWS_API_KEY    = os.getenv("NEWS_API_KEY", "")
MAX_SQL_RETRIES = 3


# ════════════════════════════════════════════════════════════════
# AGENT STATE
# Shared across all agents. Every agent reads and updates this.
# ════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    # ── Inputs ────────────────────────────────────────────────
    user_query:   str
    schema_text:  str
    dataset:      object        # SQLAlchemy Dataset object

    # ── Router decision ───────────────────────────────────────
    route:        str           # "sql" | "explain"

    # ── Planning ──────────────────────────────────────────────
    plan:         Optional[str]  # steps the planning agent decided

    # ── SQL ───────────────────────────────────────────────────
    generated_sql:   Optional[str]
    sql_explanation: Optional[str]
    sql_valid:       bool
    sql_attempts:    int
    sql_error:       Optional[str]

    # ── Error classification ──────────────────────────────────
    error_type:      Optional[str]  # "column_not_found"|"table_not_found"|"syntax"|"other"
    error_strategy:  Optional[str]  # what fix_sql should do

    # ── Results ───────────────────────────────────────────────
    result_df:    Optional[object]
    result_rows:  List[dict]
    row_count:    int

    # ── Result validation ─────────────────────────────────────
    result_valid:   bool
    result_issue:   Optional[str]  # "empty" | "too_many_rows" | None

    # ── Web context ───────────────────────────────────────────
    scraped_context: Optional[str]

    # ── AI outputs ────────────────────────────────────────────
    insights:        List[dict]
    recommendations: List[dict]
    chart_config:    Optional[dict]
    explanation:     Optional[str]

    # ── Final ─────────────────────────────────────────────────
    final_answer:      Optional[str]
    error:             Optional[str]
    execution_time_ms: int


# ════════════════════════════════════════════════════════════════
# WEB SCRAPING
# ════════════════════════════════════════════════════════════════

def scrape_google_news_rss(query: str, max_results: int = 5) -> List[dict]:
    """Google News RSS — free, no API key needed."""
    encoded = urllib.parse.quote(query)
    url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read()
        root    = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []
        articles = []
        for item in channel.findall("item")[:max_results]:
            title = item.findtext("title", "").strip()
            desc  = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()
            pub   = item.findtext("pubDate", "").strip()
            if title:
                articles.append({
                    "title":       title,
                    "description": desc[:300],
                    "published":   pub,
                    "source":      "Google News",
                })
        return articles
    except Exception:
        return []


def scrape_newsapi(query: str, max_results: int = 5) -> List[dict]:
    """NewsAPI — free tier (100 req/day), needs NEWS_API_KEY in .env."""
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        articles = []
        for art in data.get("articles", [])[:max_results]:
            title = art.get("title", "").strip()
            desc  = art.get("description", "").strip()
            src   = art.get("source", {}).get("name", "NewsAPI")
            if title and title != "[Removed]":
                articles.append({
                    "title":       title,
                    "description": desc[:300],
                    "published":   art.get("publishedAt", ""),
                    "source":      src,
                })
        return articles
    except Exception:
        return []


def fetch_web_context(user_query: str, insights: List[dict]) -> str:
    """Fetch real-world context. Google News first, NewsAPI fallback."""
    # Build focused search query
    clean = re.sub(
        r"\b(show|me|top|bottom|count|sum|avg|max|min|by|where|from|select)\b",
        "", user_query.lower(), flags=re.IGNORECASE,
    ).strip()
    if insights:
        words = insights[0].get("text", "").split()[:5]
        clean = f"{clean} {' '.join(words)}"
    search_query = clean.strip()[:100]

    if not search_query:
        return ""

    articles = scrape_google_news_rss(search_query) or scrape_newsapi(search_query)
    if not articles:
        return ""

    lines = [f"Recent news related to '{search_query}':\n"]
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. {art['title']}")
        if art["description"]:
            lines.append(f"   {art['description']}")
        lines.append(f"   Source: {art['source']} | {art['published'][:16]}\n")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# AGENT 1 — ROUTER AGENT
# Decides: does this need SQL or just explanation?
# ════════════════════════════════════════════════════════════════

def router_agent(state: AgentState) -> AgentState:
    """
    Reads the user query and schema.
    Returns 'sql' or 'explain'.
    temperature=0.0 for deterministic routing.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a query router for an AI data analyst.\n"
                "Reply with ONLY one word:\n"
                "- 'sql'     → query needs data retrieval or computation\n"
                "- 'explain' → query is conceptual, no data needed\n\n"
                "Examples:\n"
                "'show top 5 salaries' → sql\n"
                "'how many rows' → sql\n"
                "'what is machine learning' → explain\n"
                "'describe this dataset' → explain\n"
                "'average salary by role' → sql\n"
                "'compare Q1 vs Q2 revenue' → sql\n"
            ),
        },
        {
            "role": "user",
            "content": f"Schema:\n{state['schema_text']}\n\nQuery: {state['user_query']}",
        },
    ]
    try:
        decision = call_groq(messages, max_tokens=10, temperature=0.0)
        route    = "sql" if "sql" in decision.lower() else "explain"
    except Exception:
        route = "sql"
    return {**state, "route": route}


# ════════════════════════════════════════════════════════════════
# AGENT 2 — PLANNING AGENT
# Breaks complex queries into steps before SQL generation.
# ════════════════════════════════════════════════════════════════

def planning_agent(state: AgentState) -> AgentState:
    """
    For complex queries, creates a step-by-step plan.
    For simple queries, returns a one-step plan.

    Example:
    Query: "Compare Q1 vs Q2 sales and show trend"
    Plan:
      1. Get Q1 total sales
      2. Get Q2 total sales
      3. Calculate difference
      4. Order by time period
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a data analysis planner.\n"
                "Given a user query and dataset schema, create a brief SQL execution plan.\n\n"
                "For simple queries: write 1 step.\n"
                "For complex queries: write 2-4 steps.\n\n"
                "Format: numbered list only. No extra explanation.\n\n"
                f"Schema:\n{state['schema_text']}"
            ),
        },
        {
            "role": "user",
            "content": f"Create a plan for: {state['user_query']}",
        },
    ]
    try:
        plan = call_groq(messages, max_tokens=200, temperature=0.1)
        return {**state, "plan": plan}
    except Exception:
        return {**state, "plan": f"1. Generate SQL for: {state['user_query']}"}


# ════════════════════════════════════════════════════════════════
# AGENT 3 — SQL GENERATION AGENT
# Generates SQL using the plan as context.
# ════════════════════════════════════════════════════════════════

def generate_sql_agent(state: AgentState) -> AgentState:
    """
    Generates SQL. Uses the plan from planning_agent as context
    so it knows what the full goal is before writing SQL.
    """
    plan_context = ""
    if state.get("plan"):
        plan_context = f"\nExecution plan:\n{state['plan']}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert SQL analyst.\n\n"
                "RULES:\n"
                "- Generate ONLY SELECT queries — never INSERT, UPDATE, DELETE, DROP.\n"
                "- Use EXACT table and column names from the schema.\n"
                "- Always wrap SQL in ```sql code block.\n"
                "- After the SQL, write one sentence explaining it.\n\n"
                f"Schema:\n{state['schema_text']}"
                f"{plan_context}"
            ),
        },
        {
            "role": "user",
            "content": f"Generate SQL to answer: {state['user_query']}",
        },
    ]
    try:
        response    = call_groq(messages, max_tokens=512, temperature=0.1)
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
            "error":         f"SQL generation failed: {str(e)}",
        }


# ════════════════════════════════════════════════════════════════
# AGENT 4 — SQL VALIDATION AGENT
# Checks SQL safety BEFORE running it.
# ════════════════════════════════════════════════════════════════

def validate_sql_agent(state: AgentState) -> AgentState:
    """
    Validates SQL using is_safe_sql from ai.py.
    Checks:
    - Is it a SELECT query?
    - Does it contain forbidden operations?
    - Does it reference tables that exist in the schema?
    """
    sql = state.get("generated_sql", "")

    if not sql:
        return {**state, "sql_valid": False, "sql_error": "No SQL was generated"}

    # Safety check using existing function from ai.py
    if not is_safe_sql(sql):
        return {
            **state,
            "sql_valid": False,
            "sql_error": "SQL contains forbidden operations — only SELECT allowed",
        }

    # Check if referenced tables exist in schema
    table_names = [t.table_name for t in state["dataset"].tables]
    sql_lower   = sql.lower()
    missing_tables = [
        t for t in table_names
        if t.lower() not in sql_lower
    ]
    # Only warn if ALL tables are missing (might be a join)
    if table_names and len(missing_tables) == len(table_names):
        return {
            **state,
            "sql_valid": False,
            "sql_error": (
                f"SQL does not reference any known table. "
                f"Available tables: {', '.join(table_names)}"
            ),
        }

    return {**state, "sql_valid": True, "sql_error": None}


# ════════════════════════════════════════════════════════════════
# AGENT 5 — ERROR CLASSIFIER AGENT
# Classifies what went wrong so fix_sql_agent knows the strategy.
# ════════════════════════════════════════════════════════════════

def error_classifier_agent(state: AgentState) -> AgentState:
    """
    Instead of blindly retrying, this agent CLASSIFIES the error.

    Error types:
    - column_not_found → fix column name in SQL
    - table_not_found  → recheck schema, fix table name
    - syntax_error     → regenerate SQL from scratch
    - empty_result     → relax WHERE clause or LIMIT
    - other            → general fix attempt

    This makes retries smarter — the fix_sql_agent knows
    exactly what strategy to use.
    """
    error = state.get("sql_error", "") or state.get("error", "")
    error_lower = error.lower()

    if any(w in error_lower for w in ["column", "does not exist", "no such column"]):
        error_type    = "column_not_found"
        error_strategy = "Fix the column name. Check the schema for exact column names."
    elif any(w in error_lower for w in ["table", "no such table", "relation"]):
        error_type    = "table_not_found"
        error_strategy = "Fix the table name. Use exact table names from the schema."
    elif any(w in error_lower for w in ["syntax", "parse", "unexpected"]):
        error_type    = "syntax_error"
        error_strategy = "The SQL has a syntax error. Regenerate from scratch."
    elif any(w in error_lower for w in ["empty", "no rows", "0 rows"]):
        error_type    = "empty_result"
        error_strategy = "Results are empty. Relax any WHERE conditions or remove LIMIT."
    elif "forbidden" in error_lower or "select" in error_lower:
        error_type    = "unsafe_sql"
        error_strategy = "Only SELECT queries are allowed. Remove any non-SELECT operations."
    else:
        error_type    = "other"
        error_strategy = "Unknown error. Review the SQL carefully against the schema."

    return {
        **state,
        "error_type":     error_type,
        "error_strategy": error_strategy,
    }


# ════════════════════════════════════════════════════════════════
# AGENT 6 — SQL REPAIR AGENT
# Uses error classification to fix SQL intelligently.
# ════════════════════════════════════════════════════════════════

def fix_sql_agent(state: AgentState) -> AgentState:
    """
    Self-corrects failed SQL.
    Uses error_type and error_strategy from error_classifier_agent
    so it knows exactly what to fix.
    """
    error_strategy = state.get("error_strategy", "Fix the SQL error.")
    error_type     = state.get("error_type", "other")

    # For syntax errors — regenerate from scratch
    if error_type == "syntax_error":
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert SQL analyst.\n"
                    "The previous SQL had a syntax error. "
                    "Write a completely new SQL query from scratch.\n\n"
                    "RULES:\n"
                    "- Generate ONLY SELECT queries.\n"
                    "- Use EXACT table and column names from schema.\n"
                    "- Wrap SQL in ```sql code block.\n\n"
                    f"Schema:\n{state['schema_text']}"
                ),
            },
            {
                "role": "user",
                "content": f"Regenerate SQL for: {state['user_query']}",
            },
        ]
    else:
        # For other errors — fix the specific issue
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert SQL debugger.\n\n"
                    f"Fix strategy: {error_strategy}\n\n"
                    "RULES:\n"
                    "- Apply the fix strategy above.\n"
                    "- Use EXACT table and column names from schema.\n"
                    "- Wrap fixed SQL in ```sql code block.\n\n"
                    f"Schema:\n{state['schema_text']}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original question: {state['user_query']}\n\n"
                    f"Failed SQL:\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
                    f"Error: {state.get('sql_error', '')}\n"
                    f"Error type: {error_type}\n\n"
                    "Fix the SQL."
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
            "error":        f"SQL repair failed: {str(e)}",
        }


# ════════════════════════════════════════════════════════════════
# AGENT 7 — EXECUTION AGENT
# Runs SQL on dataset using DuckDB (from ai.py).
# ════════════════════════════════════════════════════════════════

def execute_sql_agent(state: AgentState) -> AgentState:
    """
    Executes SQL using execute_sql_duckdb from ai.py.
    Records exact error message for error_classifier_agent.
    """
    sql = state.get("generated_sql", "")
    if not sql:
        return {**state, "sql_error": "No SQL was generated"}

    start = time.time()
    try:
        df          = execute_sql_duckdb(sql, state["dataset"])
        exec_ms     = int((time.time() - start) * 1000)
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


# ════════════════════════════════════════════════════════════════
# AGENT 8 — RESULT VALIDATOR AGENT
# Checks if the results actually make sense.
# ════════════════════════════════════════════════════════════════

def result_validator_agent(state: AgentState) -> AgentState:
    """
    Validates query results AFTER execution.

    Checks:
    - Empty result (0 rows) → needs query relaxation
    - Too many rows (>50k) → needs LIMIT
    - Results are valid → proceed

    This prevents passing meaningless results to insights.
    """
    row_count = state.get("row_count", 0)
    result_rows = state.get("result_rows", [])

    if row_count == 0:
        return {
            **state,
            "result_valid":  False,
            "result_issue":  "empty",
            "sql_error":     (
                "Query returned 0 rows. "
                "The WHERE conditions may be too restrictive."
            ),
        }

    if row_count > 50000:
        # Too many rows — not an error, just add note
        return {
            **state,
            "result_valid": True,
            "result_issue": "too_many_rows",
            # Truncate to 100 preview rows (already done in execute_sql_agent)
        }

    return {**state, "result_valid": True, "result_issue": None}


# ════════════════════════════════════════════════════════════════
# AGENT 9 — EXPLAIN AGENT
# Handles non-SQL conceptual queries.
# ════════════════════════════════════════════════════════════════

def explain_agent(state: AgentState) -> AgentState:
    """Answers conceptual queries without SQL."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert AI Data Analyst. "
                "Answer the user's question clearly and concisely. "
                "Reference the dataset schema if relevant.\n\n"
                f"Dataset schema:\n{state['schema_text']}"
            ),
        },
        {"role": "user", "content": state["user_query"]},
    ]
    try:
        answer = call_groq(messages, max_tokens=512, temperature=0.3)
        return {**state, "explanation": answer, "final_answer": answer}
    except Exception as e:
        fallback = "I could not generate an explanation. Please try again."
        return {**state, "explanation": fallback, "final_answer": fallback, "error": str(e)}


# ════════════════════════════════════════════════════════════════
# AGENT 10 — INSIGHTS AGENT
# ════════════════════════════════════════════════════════════════

def insights_agent(state: AgentState) -> AgentState:
    """Generates 3 data-driven insights from results."""
    if not state.get("result_rows"):
        return {**state, "insights": []}

    sample = json.dumps(state["result_rows"][:20], default=str)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a business intelligence analyst.\n"
                "Generate exactly 3 specific, data-driven insights.\n\n"
                "Respond ONLY with a JSON array:\n"
                '[{"insight": "...", "importance": 0.9}, ...]'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {state['user_query']}\n"
                f"Total rows: {state['row_count']}\n"
                f"Sample data:\n{sample}"
            ),
        },
    ]
    try:
        response = call_groq(messages, max_tokens=512, temperature=0.2)
        clean    = re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()
        s        = clean.find("[")
        e        = clean.rfind("]") + 1
        if s >= 0 and e > s:
            data     = json.loads(clean[s:e])
            insights = [
                {"text": d.get("insight", ""), "score": float(d.get("importance", 0.5))}
                for d in data if d.get("insight")
            ]
            return {**state, "insights": insights}
    except Exception:
        pass
    return {**state, "insights": []}


# ════════════════════════════════════════════════════════════════
# SCRAPE WEB AGENT
# ════════════════════════════════════════════════════════════════

def scrape_web_agent(state: AgentState) -> AgentState:
    """
    Fetches real-world news context.
    Google News RSS first, NewsAPI fallback.
    Never crashes — returns empty string on failure.
    """
    try:
        context = fetch_web_context(
            user_query=state["user_query"],
            insights=state.get("insights", []),
        )
        return {**state, "scraped_context": context}
    except Exception:
        return {**state, "scraped_context": ""}


# ════════════════════════════════════════════════════════════════
# RECOMMENDATIONS AGENT
# Backed by data + insights + real web news
# ════════════════════════════════════════════════════════════════

def recommendations_agent(state: AgentState) -> AgentState:
    """
    Generates 3 actionable recommendations.
    Grounded in:
    1. Query results
    2. AI insights
    3. Real-world news (from scrape_web_agent)
    """
    if not state.get("insights"):
        return {**state, "recommendations": []}

    insights_text   = "\n".join(f"- {i['text']}" for i in state["insights"])
    scraped_context = state.get("scraped_context", "")

    web_section = ""
    if scraped_context:
        web_section = (
            f"\nReal-world context (current news):\n{scraped_context}\n\n"
            "Use this to make recommendations more current and grounded."
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a strategic business consultant.\n"
                "Give exactly 3 specific, actionable recommendations "
                "tied directly to the data findings.\n"
                f"{web_section}\n\n"
                "Respond ONLY with a JSON array:\n"
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
        s        = clean.find("[")
        e        = clean.rfind("]") + 1
        if s >= 0 and e > s:
            data = json.loads(clean[s:e])
            recs = [
                {"text": d.get("recommendation", ""), "score": float(d.get("confidence", 0.5))}
                for d in data if d.get("recommendation")
            ]
            return {**state, "recommendations": recs}
    except Exception:
        pass
    return {**state, "recommendations": []}


# ════════════════════════════════════════════════════════════════
# CHART AGENT
# ════════════════════════════════════════════════════════════════

def chart_agent(state: AgentState) -> AgentState:
    """Generates chart config using generate_chart_config from ai.py."""
    df = state.get("result_df")
    if df is None or not state.get("result_rows"):
        return {**state, "chart_config": None}
    try:
        chart_config = generate_chart_config(df, state["user_query"])
        return {**state, "chart_config": chart_config}
    except Exception:
        return {**state, "chart_config": None}


# ════════════════════════════════════════════════════════════════
# FINAL AGENT
# ════════════════════════════════════════════════════════════════

def final_agent(state: AgentState) -> AgentState:
    """Assembles final answer string."""
    if state.get("route") == "explain":
        return state

    error    = state.get("error") or state.get("sql_error")
    attempts = state.get("sql_attempts", 0)

    if error and not state.get("result_rows"):
        final = (
            f"Analysis failed after {attempts} attempt(s). "
            f"Error type: {state.get('error_type', 'unknown')}. "
            f"Detail: {error}. "
            "Please rephrase your question."
        )
    else:
        n_ins  = len(state.get("insights", []))
        n_recs = len(state.get("recommendations", []))
        has_web = bool(state.get("scraped_context"))
        issue  = state.get("result_issue", "")
        note   = " (large dataset — showing preview)" if issue == "too_many_rows" else ""
        final = (
            f"Analysis complete{note}. "
            f"Found {state.get('row_count', 0)} rows. "
            f"Generated {n_ins} insights and {n_recs} recommendations"
            f"{' backed by real-world news' if has_web else ''}."
        )

    return {**state, "final_answer": final}


# ════════════════════════════════════════════════════════════════
# CONDITIONAL EDGES
# ════════════════════════════════════════════════════════════════

def route_after_router(state: AgentState) -> str:
    return "planning" if state.get("route") == "sql" else "explain"


def route_after_validate_sql(state: AgentState) -> str:
    if state.get("sql_valid"):
        return "execute_sql"
    attempts = state.get("sql_attempts", 0)
    return "classify_error" if attempts < MAX_SQL_RETRIES else "final"


def route_after_execute(state: AgentState) -> str:
    if not state.get("sql_error"):
        return "validate_result"
    attempts = state.get("sql_attempts", 0)
    return "classify_error" if attempts < MAX_SQL_RETRIES else "final"


def route_after_classify_error(state: AgentState) -> str:
    """Always routes to fix_sql after classification."""
    return "fix_sql"


def route_after_fix_sql(state: AgentState) -> str:
    """After fixing SQL, re-validate it."""
    return "validate_sql"


def route_after_validate_result(state: AgentState) -> str:
    if state.get("result_valid"):
        return "insights"
    # Empty result — try to fix SQL (relax query), but only if attempts remain
    attempts = state.get("sql_attempts", 0)
    return "classify_error" if attempts < MAX_SQL_RETRIES else "final"


# ════════════════════════════════════════════════════════════════
# BUILD THE GRAPH
# ════════════════════════════════════════════════════════════════

def build_agent():
    graph = StateGraph(AgentState)

    # Register all 10 agents as nodes
    graph.add_node("router",          router_agent)
    graph.add_node("planning",        planning_agent)
    graph.add_node("generate_sql",    generate_sql_agent)
    graph.add_node("validate_sql",    validate_sql_agent)
    graph.add_node("classify_error",  error_classifier_agent)
    graph.add_node("fix_sql",         fix_sql_agent)
    graph.add_node("execute_sql",     execute_sql_agent)
    graph.add_node("validate_result", result_validator_agent)
    graph.add_node("explain",         explain_agent)
    graph.add_node("insights",        insights_agent)
    graph.add_node("scrape_web",      scrape_web_agent)
    graph.add_node("recommendations", recommendations_agent)
    graph.add_node("chart",           chart_agent)
    graph.add_node("final",           final_agent)

    # Entry
    graph.set_entry_point("router")

    # Router → planning or explain
    graph.add_conditional_edges("router", route_after_router, {
        "planning": "planning",
        "explain":  "explain",
    })

    # Planning → generate SQL
    graph.add_edge("planning", "generate_sql")

    # Generate SQL → validate SQL
    graph.add_edge("generate_sql", "validate_sql")

    # Validate SQL → execute or classify error
    graph.add_conditional_edges("validate_sql", route_after_validate_sql, {
        "execute_sql":    "execute_sql",
        "classify_error": "classify_error",
        "final":          "final",
    })

    # Execute SQL → validate result or classify error
    graph.add_conditional_edges("execute_sql", route_after_execute, {
        "validate_result": "validate_result",
        "classify_error":  "classify_error",
        "final":           "final",
    })

    # Classify error → fix SQL
    graph.add_conditional_edges("classify_error", route_after_classify_error, {
        "fix_sql": "fix_sql",
    })

    # Fix SQL → re-validate SQL
    graph.add_conditional_edges("fix_sql", route_after_fix_sql, {
        "validate_sql": "validate_sql",
    })

    # Validate result → insights or classify error
    graph.add_conditional_edges("validate_result", route_after_validate_result, {
        "insights":       "insights",
        "classify_error": "classify_error",
        "final":          "final",
    })

    # Linear pipeline after validation
    graph.add_edge("insights",        "scrape_web")
    graph.add_edge("scrape_web",      "recommendations")
    graph.add_edge("recommendations", "chart")
    graph.add_edge("chart",           "final")
    graph.add_edge("explain",         "final")
    graph.add_edge("final",           END)

    return graph.compile()


# ════════════════════════════════════════════════════════════════
# PUBLIC FUNCTION — called from routers/ai.py
# ════════════════════════════════════════════════════════════════

def run_agent(
    user_query:  str,
    schema_text: str,
    dataset:     object,
) -> AgentState:
    """Run the multi-agent graph and return final state."""
    initial: AgentState = {
        "user_query":        user_query,
        "schema_text":       schema_text,
        "dataset":           dataset,
        "route":             "",
        "plan":              None,
        "generated_sql":     None,
        "sql_explanation":   None,
        "sql_valid":         False,
        "sql_attempts":      0,
        "sql_error":         None,
        "error_type":        None,
        "error_strategy":    None,
        "result_df":         None,
        "result_rows":       [],
        "row_count":         0,
        "result_valid":      False,
        "result_issue":      None,
        "scraped_context":   None,
        "insights":          [],
        "recommendations":   [],
        "chart_config":      None,
        "explanation":       None,
        "final_answer":      None,
        "error":             None,
        "execution_time_ms": 0,
    }

    agent  = build_agent()
    result = agent.invoke(initial)
    return result