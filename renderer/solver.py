"""Опційний, підключуваний гачок під капч-солвер.

Вимкнений за замовчуванням. Вмикається лише коли задані env-змінні
`CAPTCHA_PROVIDER` (наразі підтримується `2captcha`) та `CAPTCHA_API_KEY`.

⚠️  Застереження: інтерактивні капчі (reCAPTCHA/hCaptcha) алгоритмічно не
вирішуються. Цей шлях покладається на платний зовнішній сервіс (людські ферми),
коштує гроші за кожен розв'язок і часто ПОРУШУЄ ToS сайту. Тримати вимкненим,
якщо немає чіткого дозволу на конкретний сайт. Код — експериментальний скелет.
"""
import asyncio
import logging
import os

import httpx

log = logging.getLogger("renderer.solver")

PROVIDER = os.getenv("CAPTCHA_PROVIDER", "").strip().lower()
API_KEY = os.getenv("CAPTCHA_API_KEY", "").strip()

# Детект sitekey найпоширеніших капч прямо на сторінці.
_SITEKEY_JS = """
() => {
  const re = document.querySelector('.g-recaptcha[data-sitekey], [data-sitekey]');
  if (re) return { type: re.classList.contains('h-captcha') ? 'hcaptcha' : 'recaptcha',
                   sitekey: re.getAttribute('data-sitekey') };
  const ifr = document.querySelector("iframe[src*='hcaptcha.com']");
  if (ifr) { const m = ifr.src.match(/sitekey=([^&]+)/); if (m) return { type: 'hcaptcha', sitekey: m[1] }; }
  return null;
}
"""


async def solve_if_present(page) -> bool:
    """Спробувати розв'язати капчу на сторінці. Повертає True, якщо токен інʼєктовано."""
    if not (PROVIDER and API_KEY):
        return False  # вимкнено — це штатний шлях
    try:
        info = await page.evaluate(_SITEKEY_JS)
    except Exception:
        info = None
    if not info:
        return False

    if PROVIDER == "2captcha":
        token = await _solve_2captcha(info["type"], info["sitekey"], page.url)
        if token:
            await _inject_token(page, info["type"], token)
            return True
        return False

    log.warning("unknown CAPTCHA_PROVIDER=%r", PROVIDER)
    return False


async def _solve_2captcha(captcha_type: str, sitekey: str, page_url: str) -> str | None:
    method = "hcaptcha" if captcha_type == "hcaptcha" else "userrecaptcha"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://2captcha.com/in.php",
            data={"key": API_KEY, "method": method, "sitekey": sitekey,
                  "pageurl": page_url, "json": 1},
        )
        data = r.json()
        if data.get("status") != 1:
            log.warning("2captcha submit failed: %s", data)
            return None
        cap_id = data["request"]

        # Полінг результату (солвери відповідають за 15–60с).
        for _ in range(24):
            await asyncio.sleep(5)
            res = await client.get(
                "https://2captcha.com/res.php",
                params={"key": API_KEY, "action": "get", "id": cap_id, "json": 1},
            )
            rd = res.json()
            if rd.get("status") == 1:
                return rd["request"]
            if rd.get("request") != "CAPCHA_NOT_READY":
                log.warning("2captcha poll error: %s", rd)
                return None
    return None


async def _inject_token(page, captcha_type: str, token: str) -> None:
    field = "h-captcha-response" if captcha_type == "hcaptcha" else "g-recaptcha-response"
    await page.evaluate(
        """([field, token]) => {
            let el = document.querySelector(`textarea[name="${field}"], #${field}`);
            if (!el) {
                el = document.createElement('textarea');
                el.name = field; el.id = field; el.style.display = 'none';
                document.body.appendChild(el);
            }
            el.value = token;
        }""",
        [field, token],
    )
