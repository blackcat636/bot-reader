# Telegram Reader Bot

Бот отримує посилання на статтю і повертає її у форматі PDF — без реклами, банерів, спливаючих вікон і коментарів (режим читача).

## Можливості

- Виділення тексту статті через алгоритм Mozilla Readability (той самий, що у Firefox Reader View)
- Генерація PDF з чистою типографікою: шрифт Georgia, береги, нумерація сторінок
- Назва файлу PDF відповідає заголовку статті
- Повідомлення про помилки: недоступний сайт, paywall, не-HTML сторінки

---

## Запуск

### Docker (рекомендовано)

```bash
cp .env.example .env
# Відредагувати .env — вписати токен

docker compose up -d --build
```

Корисні команди:

```bash
docker compose logs -f     # логи в реальному часі
docker compose restart     # перезапуск
docker compose down        # зупинка
```

### Без Docker

```bash
# Системні залежності (Debian/Ubuntu)
sudo apt-get install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 fonts-liberation fonts-dejavu-core

pip install -r requirements.txt
cp .env.example .env
python bot.py
```

---

## Конфігурація `.env`

| Змінна | Обовʼязкова | Опис |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен від [@BotFather](https://t.me/BotFather) |

---

## Команди бота

| Команда / дія | Опис |
|---|---|
| `/start` | Привітання та інструкція |
| Будь-який текст з URL | Завантажити статтю і повернути PDF |

---

## Флоу

```
Користувач надсилає URL
  └─► Бот завантажує сторінку (httpx)
        └─► readability-lxml виділяє чистий HTML
              └─► weasyprint рендерить PDF
                    └─► Бот надсилає PDF-файл
```

---

## Обмеження

| Ситуація | Що станеться |
|---|---|
| Paywall / авторизація | Бот повідомить, що не вдалося виділити текст |
| SPA на React/Vue без SSR | Контент може бути порожній — JS не виконується |
| Сайт блокує запити | Помилка HTTP або порожній вміст |
| Не-HTML посилання (PDF, зображення) | Бот повідомить про невідповідний тип контенту |
| Сайт відповідає більше 30 с | Timeout з повідомленням |

---

## Структура проекту

```
reader-bot/
├── bot.py              # Основний код бота
├── Dockerfile
├── docker-compose.yml
├── .env                # Конфігурація (не комітити)
├── .env.example        # Приклад конфігурації
├── requirements.txt    # Залежності
└── README.md
```

---

## Залежності

| Пакет | Версія | Призначення |
|---|---|---|
| `python-telegram-bot` | 21.9 | Telegram Bot API |
| `httpx` | 0.27.2 | Завантаження сторінок |
| `readability-lxml` | 0.8.1 | Виділення тексту статті (Mozilla Readability) |
| `weasyprint` | 62.3 | Рендеринг HTML → PDF |
| `python-dotenv` | 1.0.1 | Читання `.env` файлу |

### Системні залежності (встановлюються у Dockerfile)

`libcairo2`, `libpango`, `libgdk-pixbuf` — рендеринг для weasyprint.  
`fonts-liberation`, `fonts-dejavu-core` — базові шрифти для PDF.
