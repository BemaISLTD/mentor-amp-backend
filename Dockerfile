# API image with a recorded source revision and bundled demo inputs.
FROM python:3.12-slim AS source-identity

# The final image contains only the source revision, not repository metadata.
COPY .git/ /source/.git/
RUN python -c "from pathlib import Path; root=Path('/source/.git'); head=(root/'HEAD').read_text().strip(); ref=head.removeprefix('ref: ').strip() if head.startswith('ref: ') else None; loose=root/ref if ref else None; packed=(root/'packed-refs').read_text().splitlines() if (root/'packed-refs').exists() else []; sha=(loose.read_text().strip() if loose and loose.exists() else next((line.split()[0] for line in packed if ref and line.endswith(' '+ref)), head if ref is None else '')); assert len(sha)==40 and all(c in '0123456789abcdef' for c in sha.lower()), 'A Git commit is required for the Docker build'; Path('/source-commit').write_text(sha+'\n')"

FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY samples/ /samples/
COPY --from=source-identity /source-commit /source-commit

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
