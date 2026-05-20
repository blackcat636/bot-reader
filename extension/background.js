chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "save-to-reader",
    title: chrome.i18n.getMessage("contextMenuSave"),
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
      message: chrome.i18n.getMessage("notifErrorNoApi"),
    });
    return;
  }

  const url = tab.url;
  const lang = chrome.i18n.getUILanguage().split("-")[0];

  try {
    const resp = await fetch(`${apiUrl}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, user_id: userId, user_type: "browser", lang }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icon48.png",
        title: chrome.i18n.getMessage("notifErrorTitle"),
        message: data.detail || chrome.i18n.getMessage("errorFetchFailed"),
      });
      return;
    }

    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon48.png",
      title: "Reader Bot",
      message: chrome.i18n.getMessage("notifSaved", [data.title]),
    });

    await updateBadge(apiUrl, userId);
  } catch {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon48.png",
      title: chrome.i18n.getMessage("notifErrorTitle"),
      message: chrome.i18n.getMessage("notifErrorConnect"),
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
