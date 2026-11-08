// The sidebar's count of open items on the review page ("Zu prüfen"). The page announces
// each fresh load of its list with this window event, so the count follows an answer at
// once instead of at the next poll; its detail is the number of items the page now shows.
// Without a number the sidebar fetches GET /inbox itself.
export const INBOX_CHANGED = "hausbuch:inbox-changed";

export function notifyInboxChanged(count?: number): void {
  window.dispatchEvent(new CustomEvent(INBOX_CHANGED, { detail: count }));
}
