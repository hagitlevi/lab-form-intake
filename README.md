# lab-form-intake

Turns scanned lab commitment forms into structured, technician-reviewed records.

## Local setup

1. Start Postgres:
   ```
   docker run -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:18
   ```
2. Copy `.env.example` to `.env` and fill in `DATABASE_URL` / `ANTHROPIC_API_KEY`.
3. Install backend deps and apply the schema:
   ```
   pip install -r requirements.txt
   python scripts/apply_schema.py
   ```
4. Run the backend:
   ```
   uvicorn backend.main:app --reload
   ```
   `GET /api/health` should return `{"status": "ok"}`.
5. Run the frontend (separate terminal):
   ```
   cd frontend
   npm install
   npm run dev
   ```
   Vite proxies `/api` to `http://localhost:8000`.

## Production

The backend serves `frontend/dist` (built via `npm run build`) as static files, so
one service serves both the API and the UI.
