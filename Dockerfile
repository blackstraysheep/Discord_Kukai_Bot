FROM python:3.11

RUN apt-get update && apt-get install -y \
    texlive-luatex \
    texlive-lang-japanese \
    texlive-fonts-recommended \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

RUN printf '%s\n' '\documentclass{jlreq}' '\begin{document}' 'test' '\end{document}' > /tmp/warmup.tex \
 && cd /tmp && lualatex --interaction=nonstopmode warmup.tex \
 && rm -f /tmp/warmup.*

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
