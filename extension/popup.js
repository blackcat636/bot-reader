const statusEl = document.getElementById("status");
const urlEl = document.getElementById("url");
const apiInput = document.getElementById("api-url-input");
const saveBtn = document.getElementById("save-btn");

function getOrCreateUserId() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["userId", "apiUrl"], (data) => {
      let userId = data.userId;
      if (!userId) {
        userId = crypto.randomUUID();
        chrome.storage.local.set({ userId });
      }
      if (data.apiUrl) apiInput.value = data.apiUrl;
      resolve({ userId, apiUrl: data.apiUrl || "" });
    });
  });
}

function setStatus(text, error = false) {
  statusEl.textContent = text;
  statusEl.style.color = error ? "#c00" : "#555";
}

function setButtons(disabled) {
  document.querySelectorAll("button[data-fmt]").forEach((b) => {
    b.disabled = disabled;
  });
}

async function convert(fmt, url, userId, apiUrl) {
  setButtons(true);
  setStatus("⏳ Завантажую сторінку…");

  try {
    const extractResp = await fetch(`${apiUrl}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, user_id: userId }),
    });

    if (!extractResp.ok) {
      const err = await extractResp.json().catch(() => ({}));
      setStatus(err.detail || "Помилка при завантаженні.", true);
      setButtons(false);
      return;
    }

    const article = await extractResp.json();
    setStatus("⏳ Генерую файл…");

    const dlResp = await fetch(
      `${apiUrl}/articles/${article.id}/download?format=${fmt}&user_id=${userId}`
    );

    if (!dlResp.ok) {
      setStatus("Помилка генерації файлу.", true);
      setButtons(false);
      return;
    }

    const blob = await dlResp.blob();
    const cd = dlResp.headers.get("content-disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `article.${fmt}`;

    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(blobUrl);

    setStatus("✅ Файл збережено!");
    loadRecent(savedApi);
  } catch (e) {
    setStatus("Не вдалося зв'язатися з API. Перевір налаштування.", true);
  }

  setButtons(false);
}

(async () => {
  const { userId, apiUrl } = await getOrCreateUserId();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const currentUrl = tab?.url || "";
  urlEl.textContent = currentUrl;

  // Завантажуємо останню історію
  async function loadRecent(api) {
    if (!api) return;
    try {
      const resp = await fetch(`${api}/history?user_id=${userId}&limit=5`);
      const data = await resp.json();
      const list = document.getElementById("recent-list");
      if (!data.items?.length) {
        list.textContent = "Поки порожньо.";
        return;
      }
      list.innerHTML = "";
      data.items.forEach((a) => {
        const el = document.createElement("div");
        el.className = "recent-item";
        el.title = a.title;
        el.textContent = a.title;
        el.addEventListener("click", () => chrome.tabs.create({ url: a.url }));
        list.appendChild(el);
      });
      const header = document.getElementById("recent-header");
      header.textContent = `📚 Остання історія (всього: ${data.total})`;

      // badge
      const label = data.total > 99 ? "99+" : String(data.total);
      chrome.action.setBadgeText({ text: label });
      chrome.action.setBadgeBackgroundColor({ color: "#1a5fa8" });
    } catch {
      document.getElementById("recent-list").textContent = "Не вдалося завантажити.";
    }
  }

  const savedApi = apiInput.value.trim().replace(/\/$/, "");
  if (savedApi) loadRecent(savedApi);

  document.querySelectorAll("button[data-fmt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const savedApi = apiInput.value.trim().replace(/\/$/, "");
      if (!savedApi) {
        setStatus("Спочатку вкажи API URL в налаштуваннях.", true);
        return;
      }
      convert(btn.dataset.fmt, currentUrl, userId, savedApi);
    });
  });

  saveBtn.addEventListener("click", () => {
    const val = apiInput.value.trim().replace(/\/$/, "");
    chrome.storage.local.set({ apiUrl: val });
    setStatus("API URL збережено.");
  });

  const shareStatus = document.getElementById("share-status");
  document.getElementById("share-claim-btn").addEventListener("click", async () => {
    const code = document.getElementById("share-code-input").value.trim().toUpperCase();
    const api = apiInput.value.trim().replace(/\/$/, "");
    if (!code || !api) { shareStatus.textContent = "Вкажи код та API URL."; return; }
    shareStatus.textContent = "⏳ Отримую…";
    try {
      const resp = await fetch(`${api}/share/claim`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, user_id: userId, user_type: "browser" }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        shareStatus.textContent = data.detail || "Помилка.";
      } else {
        shareStatus.textContent = `✅ Додано: «${data.title}»`;
        document.getElementById("share-code-input").value = "";
      }
    } catch {
      shareStatus.textContent = "Не вдалося зв'язатися з API.";
    }
  });

  const linkStatus = document.getElementById("link-status");
  const linkCodeInput = document.getElementById("link-code-input");

  let pendingCode = null;

  document.getElementById("link-confirm-btn").addEventListener("click", async () => {
    const api = apiInput.value.trim().replace(/\/$/, "");

    // Якщо вже є preview — підтверджуємо злиття
    if (pendingCode) {
      linkStatus.textContent = "⏳ Зливаю…";
      try {
        const resp = await fetch(`${api}/link/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: pendingCode, user_id: userId, user_type: "browser" }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          linkStatus.textContent = data.detail || "Помилка.";
        } else {
          linkStatus.textContent = `✅ Готово! Акаунтів у групі: ${data.merged_users}`;
          linkCodeInput.value = "";
        }
      } catch {
        linkStatus.textContent = "Не вдалося зв'язатися з API.";
      }
      pendingCode = null;
      document.getElementById("link-confirm-btn").textContent = "Прив'язати";
      return;
    }

    // Перший клік — показуємо preview
    const code = linkCodeInput.value.trim().toUpperCase();
    if (!code || !api) {
      linkStatus.textContent = "Вкажи код та API URL.";
      return;
    }
    linkStatus.textContent = "⏳ Перевіряю…";
    try {
      const resp = await fetch(
        `${api}/link/preview?code=${code}&user_id=${userId}&user_type=browser`
      );
      const data = await resp.json();
      if (!resp.ok) {
        linkStatus.textContent = data.detail || "Помилка.";
        return;
      }
      if (data.already_same_group) {
        linkStatus.textContent = "ℹ️ Ці акаунти вже в одній групі.";
        return;
      }
      linkStatus.textContent =
        `Твоя група: ${data.your_group_users} акаунт(ів), ${data.your_group_articles} статей\n` +
        `Інша група: ${data.code_group_users} акаунт(ів), ${data.code_group_articles} статей\n` +
        `Після злиття: ${data.total_users} акаунти, ${data.total_articles} статей`;
      pendingCode = code;
      document.getElementById("link-confirm-btn").textContent = "✅ Підтвердити злиття";
    } catch {
      linkStatus.textContent = "Не вдалося зв'язатися з API.";
    }
  });

  document.getElementById("link-generate-btn").addEventListener("click", async () => {
    const api = apiInput.value.trim().replace(/\/$/, "");
    if (!api) { linkStatus.textContent = "Вкажи API URL."; return; }
    linkStatus.textContent = "⏳ Генерую код…";
    try {
      const resp = await fetch(`${api}/link/generate?user_id=${userId}&user_type=browser`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        linkStatus.textContent = data.detail || "Помилка.";
      } else {
        linkStatus.textContent = `Код: ${data.code} (діє 10 хв)`;
      }
    } catch {
      linkStatus.textContent = "Не вдалося зв'язатися з API.";
    }
  });
})();
