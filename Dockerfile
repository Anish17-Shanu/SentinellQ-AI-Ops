FROM python:3.13-slim
WORKDIR /app
COPY . .
EXPOSE 8081
CMD ["python", "src/server.py"]
