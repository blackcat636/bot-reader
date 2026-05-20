const statusEl = document.getElementById("status");
const urlEl = document.getElementById("url");
const apiInput = document.getElementById("api-url-input");
const saveBtn = document.getElementById("save-btn");

const lang = chrome.i18n.getUILanguage().split("-")[0];

function i18n(key, ...subs) {
  return chrome.i18n.getMessage(key, subs) || key;
}

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const msg = i18n(el.dataset.i18n);
    if (msg) el.textContent = msg;
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const msg = i18n(el.dataset.i18nPlaceholder);
    if (msg) el.placeholder = msg;
  });
}

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
  setStatus(i18n("statusFetching"));

  try {
    const extractResp = await fetch(`${apiUrl}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, user_id: userId, user_type: "browser", lang }),
    });

    if (!extractResp.ok) {
      const err = await extractResp.json().catch(() => ({}));
      setStatus(err.detail || i18n("errorFetchFailed"), true);
      setButtons(false);
      return;
    }

    const article = await extractResp.json();
    setStatus(i18n("statusGenerating"));

    const dlResp = await fetch(
      `${apiUrl}/articles/${article.id}/download?format=${fmt}&user_id=${userId}&lang=${lang}`
    );

    if (!dlResp.ok) {
      setStatus(i18n("errorGenerateFailed"), true);
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

    setStatus(i18n("statusSaved"));
    loadRecent(savedApi);
  } catch (e) {
    setStatus(i18n("errorApiConnect"), true);
  }

  setButtons(false);
}

(async () => {
  applyI18n();

  const { userId, apiUrl } = await getOrCreateUserId();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const currentUrl = tab?.url || "";
  urlEl.textContent = currentUrl;

  async function loadRecent(api) {
    if (!api) return;
    try {
      const resp = await fetch(`${api}/history?user_id=${userId}&limit=5`);
      const data = await resp.json();
      const list = document.getElementById("recent-list");
      if (!data.items?.length) {
        list.textContent = i18n("recentEmpty");
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
      header.textContent = i18n("recentHeader", String(data.total));

      const label = data.total > 99 ? "99+" : String(data.total);
      chrome.action.setBadgeText({ text: label });
      chrome.action.setBadgeBackgroundColor({ color: "#1a5fa8" });
    } catch {
      document.getElementById("recent-list").textContent = i18n("recentLoadError");
    }
  }

  const savedApi = apiInput.value.trim().replace(/\/$/, "");
  if (savedApi) loadRecent(savedApi);

  document.querySelectorAll("button[data-fmt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const api = apiInput.value.trim().replace(/\/$/, "");
      if (!api) {
        setStatus(i18n("errorSetApiFirst"), true);
        return;
      }
      convert(btn.dataset.fmt, currentUrl, userId, api);
    });
  });

  saveBtn.addEventListener("click", () => {
    const val = apiInput.value.trim().replace(/\/$/, "");
    chrome.storage.local.set({ apiUrl: val });
    setStatus(i18n("apiUrlSaved"));
  });

  const shareStatus = document.getElementById("share-status");
  document.getElementById("share-claim-btn").addEventListener("click", async () => {
    const code = document.getElementById("share-code-input").value.trim().toUpperCase();
    const api = apiInput.value.trim().replace(/\/$/, "");
    if (!code || !api) { shareStatus.textContent = i18n("errorCodeAndApi"); return; }
    shareStatus.textContent = i18n("shareReceiving");
    try {
      const resp = await fetch(`${api}/share/claim`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, user_id: userId, user_type: "browser", lang }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        shareStatus.textContent = data.detail || i18n("errorFetchFailed");
      } else {
        shareStatus.textContent = i18n("shareAdded", data.title);
        document.getElementById("share-code-input").value = "";
      }
    } catch {
      shareStatus.textContent = i18n("errorApiConnect");
    }
  });

  const linkStatus = document.getElementById("link-status");
  const linkCodeInput = document.getElementById("link-code-input");
  const linkConfirmBtn = document.getElementById("link-confirm-btn");

  let pendingCode = null;

  linkConfirmBtn.addEventListener("click", async () => {
    const api = apiInput.value.trim().replace(/\/$/, "");

    if (pendingCode) {
      linkStatus.textContent = i18n("linkMerging");
      try {
        const resp = await fetch(`${api}/link/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: pendingCode, user_id: userId, user_type: "browser", lang }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          linkStatus.textContent = data.detail || i18n("errorFetchFailed");
        } else {
          linkStatus.textContent = i18n("linkMerged", String(data.merged_users));
          linkCodeInput.value = "";
        }
      } catch {
        linkStatus.textContent = i18n("errorApiConnect");
      }
      pendingCode = null;
      linkConfirmBtn.textContent = i18n("linkBtnDefault");
      return;
    }

    const code = linkCodeInput.value.trim().toUpperCase();
    if (!code || !api) {
      linkStatus.textContent = i18n("errorCodeAndApi");
      return;
    }
    linkStatus.textContent = i18n("linkChecking");
    try {
      const resp = await fetch(
        `${api}/link/preview?code=${code}&user_id=${userId}&user_type=browser&lang=${lang}`
      );
      const data = await resp.json();
      if (!resp.ok) {
        linkStatus.textContent = data.detail || i18n("errorFetchFailed");
        return;
      }
      if (data.already_same_group) {
        linkStatus.textContent = i18n("linkAlreadySame");
        return;
      }
      linkStatus.textContent =
        i18n("linkPreviewYour", String(data.your_group_users), String(data.your_group_articles)) + "\n" +
        i18n("linkPreviewOther", String(data.code_group_users), String(data.code_group_articles)) + "\n" +
        i18n("linkPreviewAfter", String(data.total_users), String(data.total_articles));
      pendingCode = code;
      linkConfirmBtn.textContent = i18n("linkConfirmBtn");
    } catch {
      linkStatus.textContent = i18n("errorApiConnect");
    }
  });

  document.getElementById("link-generate-btn").addEventListener("click", async () => {
    const api = apiInput.value.trim().replace(/\/$/, "");
    if (!api) { linkStatus.textContent = i18n("errorApiUrl"); return; }
    linkStatus.textContent = i18n("linkGenerating");
    try {
      const resp = await fetch(`${api}/link/generate?user_id=${userId}&user_type=browser&lang=${lang}`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        linkStatus.textContent = data.detail || i18n("errorFetchFailed");
      } else {
        linkStatus.textContent = i18n("linkCode", data.code);
      }
    } catch {
      linkStatus.textContent = i18n("errorApiConnect");
    }
  });
})();
