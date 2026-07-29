FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGSMITH_TRACING=false \
    SKILL_DOCTOR_STORAGE_BACKEND=file

WORKDIR /app

COPY backend ./backend
COPY public ./public
COPY benchmarks ./benchmarks
COPY reports ./reports

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e "backend[api]" \
    && mkdir -p reports/langgraph reports/benchmarks diagnostic_cases candidate_skills rejection_memory

EXPOSE 8010

CMD ["sh", "-c", "exec python -m uvicorn backend.skilldoctor.api:app --host 0.0.0.0 --port ${PORT:-8010}"]
