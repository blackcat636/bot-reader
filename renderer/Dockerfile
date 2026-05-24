# База Playwright несе системні бібліотеки для браузерів (gtk/x11/тощо) і Xvfb,
# які потрібні й для Firefox від camoufox. Сам chromium не використовуємо.
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY renderer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Патч Node-драйвера Playwright-Firefox. На сторінці-челенджі з uncaught JS-помилкою
# Firefox шле pageError БЕЗ location, а драйвер читає pageError.location.url → uncaught
# TypeError у Node кладе ВЕСЬ процес драйвера ("Connection closed while reading from the
# driver"), валячи будь-який рендер. Python-listener (page.on("pageerror")) тут не рятує —
# краш стається в Node до того, як подія дійде до Python. Підставляємо безпечні дефолти
# (url:"" / line:0 / column:0 — ті ж, що драйвер уживає сам, див. рядок _location в бандлі):
# самого ?. недостатньо, бо undefined потім завалить валідатор протоколу (expected string).
RUN set -eu; found=0; \
    for cb in $(find / -path '*playwright*/driver/package/lib/coreBundle.js' 2>/dev/null); do \
      sed -i -e 's/pageError\.location\.url/(pageError.location?.url ?? "")/g' \
             -e 's/pageError\.location\.lineNumber/(pageError.location?.lineNumber ?? 0)/g' \
             -e 's/pageError\.location\.columnNumber/(pageError.location?.columnNumber ?? 0)/g' "$cb"; \
      ! grep -q 'pageError\.location\.url' "$cb"; \
      echo "patched Playwright driver: $cb"; found=1; \
    done; \
    test "$found" = 1

# Завантажити патчений Firefox camoufox при збірці (а не на першому запиті).
RUN python -m camoufox fetch

COPY renderer/ renderer/

# camoufox у headless-режимі клікає по Turnstile координатами без потреби в дисплеї.
# --loop asyncio: playwright/camoufox subprocess-транспорт нестабільний з uvloop.
CMD ["uvicorn", "renderer.main:app", "--host", "0.0.0.0", "--port", "8001", "--loop", "asyncio"]
