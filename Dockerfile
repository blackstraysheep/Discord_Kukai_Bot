FROM python:3.11

RUN apt-get update && apt-get install -y \
    texlive-luatex \
    texlive-lang-japanese \
    texlive-fonts-recommended \
    fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
