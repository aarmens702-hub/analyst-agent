# Kernel sandbox image (spec §2). Built with network; run with --network none.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd -m -u 1000 analyst
USER 1000:1000

# The container idles; the client attaches the supervisor via `docker exec -i`.
CMD ["sleep", "infinity"]
