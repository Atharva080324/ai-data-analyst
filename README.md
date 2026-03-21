# AI Data Analyst SaaS — Full-Stack Deployment Guide

This project is a premium, full-stack B2B SaaS platform for AI-powered Data Analysis. It leverages Next.js 14, Supabase (Auth, Postgres, Storage), Anthropic Claude API, and in-browser SQLite WASM execution for maximum speed and security.

## Tech Stack Overview
- **Framework**: Next.js 14 (App Router)
- **Database / Auth / Storage**: Supabase
- **AI Engine**: Anthropic Claude 3.5 Sonnet
- **Execution Engine**: `sql.js` (SQLite compiled to WebAssembly)
- **UI & Styling**: Tailwind CSS, Framer Motion, Recharts

## Setup Instructions

### 1. Supabase Initialization
1. Create a new Supabase project at [database.new](https://database.new)
2. Go to the **SQL Editor** in your Supabase dashboard.
3. Paste and run the entire contents of `supabase_schema.sql` (found in the root directory).
4. Go to **Storage**, and ensure you have a bucket named exactly `user-datasets`.
5. Go to **Authentication > Providers** and ensure Email provider is enabled.
6. Under **Authentication > URL Configuration**, add your local URL `http://localhost:3000/api/auth/callback` to the redirect URLs.

### 2. Environment Variables
In the `frontend/` directory, create a `.env.local` file:
```env
NEXT_PUBLIC_SUPABASE_URL=your-project-url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role
ANTHROPIC_API_KEY=sk-ant-api03-...
```

### 3. Install Dependencies & Run
```bash
cd frontend
npm install
npm run dev
```

## Architecture Notes
- **Data Privacy**: When a user uploads a CSV, it's pushed to a private Supabase Storage bucket. When they enter the Analysis Workspace, the CSV string is converted into a full SQLite database *inside their browser memory* using `sql.js`.
- **LLM Interaction**: The backend `/api/analyze` route NEVER sends raw dataset chunks to Claude. It only sends the inferred schema (e.g., `{"revenue": "number"}`). Claude replies with standard SQLite SQL strings.
- **Client Execution**: The frontend takes the LLM-generated SQL and runs it locally on the high-performance WASM database via `sql.js`, instantly pumping the queried aggregates into `Recharts`. This means unlimited, free query executions once the SQL is written!

## Deployment (Vercel)
The root folder contains `vercel.json` optimized for this Next.js app.
1. Push this repository to GitHub.
2. Import the `frontend/` directory into Vercel.
3. Ensure the Build Command is `npm run build` and Output Directory is `.next`.
4. Copy the environment variables from `.env.local` into the Vercel Project Settings.
5. Deploy.
