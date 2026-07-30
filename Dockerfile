FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGSMITH_TRACING=false \
    SKILL_DOCTOR_STORAGE_BACKEND=sqlite \
    SKILL_DOCTOR_SQLITE_PATH=/data/skill-doctor.sqlite3

WORKDIR /app

COPY backend ./backend
COPY public ./public
COPY benchmarks ./benchmarks
COPY reports ./reports

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e "backend[api]" \
    && mkdir -p /data reports/langgraph reports/benchmarks diagnostic_cases candidate_skills rejection_memory

EXPOSE 8010

CMD ["python", "-m", "backend.skilldoctor.start"]
