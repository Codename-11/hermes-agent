const PTY_ATTACH_TOKEN_KEY = "hermes.pty.token.chat";

/** Return a localStorage key isolated to one dashboard profile scope. */
export function ptyAttachStorageKey(profile: string): string {
  const scope = profile.trim() || "current";
  return `${PTY_ATTACH_TOKEN_KEY}.${encodeURIComponent(scope)}`;
}

/**
 * Return the stable keep-alive token for one profile-scoped dashboard chat.
 *
 * A token must never be reused across profiles: the backend PTY registry keys
 * attachments by this opaque value and will otherwise reattach a newly-selected
 * profile to an already-running PTY from the previous profile.
 */
export function ptyAttachToken(profile: string, rotate = false): string {
  const storageKey = ptyAttachStorageKey(profile);
  let token = "";

  if (!rotate) {
    try {
      token = window.localStorage.getItem(storageKey) ?? "";
    } catch {
      // Private mode or storage blocked.
    }
  }

  if (!token) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    token = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    try {
      window.localStorage.setItem(storageKey, token);
    } catch {
      // Keep the in-memory token when persistence is unavailable.
    }
  }

  return token;
}
