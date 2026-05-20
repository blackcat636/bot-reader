chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "save-to-reader",
    title: "Зберегти в Reader Bot",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "save-to-reader") return;

  const { userId, apiUrl } = await chrome.storage.local.get(["userId", "apiUrl"]);
  if (!apiUrl) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon48.png",
      title: "Reader Bot",
      message: "Спочатку вкажи API URL у налаштуваннях розширення.",
    });
    return;
  }

  const url = tab.url;

  try {
    const resp = await fetch(`${apiUrl}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, user_id: userId, user_type: "browser" }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icon48.png",
        title: "Reader Bot — помилка",
        message: data.detail || "Не вдалося зберегти статтю.",
      });
      return;
    }

    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon48.png",
      title: "Reader Bot",
      message: `Збережено: «${data.title}»`,
    });

    await updateBadge(apiUrl, userId);
  } catch {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon48.png",
      title: "Reader Bot — помилка",
      message: "Не вдалося зв'язатися з API.",
    });
  }
});

async function updateBadge(apiUrl, userId) {
  try {
    const resp = await fetch(`${apiUrl}/history?user_id=${userId}&limit=1`);
    const data = await resp.json();
    const total = data.total || 0;
    const label = total > 99 ? "99+" : String(total);
    chrome.action.setBadgeText({ text: label });
    chrome.action.setBadgeBackgroundColor({ color: "#1a5fa8" });
  } catch {
    // badge не критичний
  }
}
