FROM python:3.11

RUN apt-get update && apt-get install -y \
    texlive-luatex \
    texlive-lang-japanese \
    texlive-fonts-recommended \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

RUN printf '%s\n' '\documentclass{jlreq}' '\begin{document}' 'test' '\end{document}' > /tmp/warmup.tex \
 && cd /tmp && lualatex --interaction=nonstopmode warmup.tex \
 && rm -f /tmp/warmup.*

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin botuser \
 && mkdir -p data \
 && chown -R botuser:botuser /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=botuser:botuser . .

USER botuser

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
