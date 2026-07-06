const MOJIBAKE_MARKERS = /[ÃÂâåæçäéè]/;

export function decodePossiblyMojibake(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || !MOJIBAKE_MARKERS.test(text)) return text;

  try {
    const bytes = Uint8Array.from(text, (ch) => ch.charCodeAt(0) & 0xff);
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes).trim();
    const originalReplacementCount = (text.match(/\uFFFD/g) ?? []).length;
    const decodedReplacementCount = (decoded.match(/\uFFFD/g) ?? []).length;
    if (decoded && decodedReplacementCount <= originalReplacementCount) return decoded;
  } catch {
    /* not latin-1 mojibake */
  }

  return text;
}

export function displayNameOrUsername(value: { displayName?: unknown; username?: unknown } | null | undefined): string {
  const displayName = decodePossiblyMojibake(value?.displayName);
  if (displayName) return displayName;
  return decodePossiblyMojibake(value?.username);
}
