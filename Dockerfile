FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py README.md app/ config/ data/ ./

EXPOSE 8080
CMD ["python", "server.py"]
