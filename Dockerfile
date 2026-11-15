FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir aiogram aiohttp
CMD ["python", "bot.py"]
