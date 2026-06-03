/**
 * Client-side PIN lock для Mini App. localStorage-only (см. план Phase 8 + 1):
 * хеш + salt + attempts. SHA-256 через Web Crypto Subtle (доступен в Telegram
 * WebView — HTTPS-контекст). 0 backend changes.
 *
 * Threat model: «случайные глаза» (партнёр заглянул в телефон), не криминал.
 * Wipe-on-5-fails — soft reset, юзер пересоздаёт PIN, доход на VPS не страдает.
 *
 * Limitations:
 *   - Wipe device → PIN потерян, нужно пересоздать.
 *   - Telegram Web vs iOS app = разный localStorage scope → PIN на каждом
 *     контексте отдельно.
 *   - Не защищает от DevTools (Telegram Mini App открывается без них для
 *     обычных юзеров — приемлемо).
 */

export const PIN_LENGTH = 4;
export const MAX_ATTEMPTS = 5;
export const LOCK_TIMEOUT_MS = 5 * 60 * 1000;

const KEY_HASH = 'pfd_pin_hash';
const KEY_SALT = 'pfd_pin_salt';
const KEY_ATTEMPTS = 'pfd_pin_attempts';
const EVT = 'pfd-pin-changed';

export function pinExists(): boolean {
  return localStorage.getItem(KEY_HASH) !== null;
}

export function getAttempts(): number {
  return Number(localStorage.getItem(KEY_ATTEMPTS) ?? '0');
}

function bufToHex(buf: ArrayBuffer | Uint8Array): string {
  const arr = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function sha256Hex(text: string): Promise<string> {
  const enc = new TextEncoder().encode(text);
  const buf = await crypto.subtle.digest('SHA-256', enc);
  return bufToHex(buf);
}

function randomSaltHex(): string {
  const buf = new Uint8Array(16);
  crypto.getRandomValues(buf);
  return bufToHex(buf);
}

export async function setPin(pin: string): Promise<void> {
  const salt = randomSaltHex();
  const hash = await sha256Hex(salt + pin);
  localStorage.setItem(KEY_SALT, salt);
  localStorage.setItem(KEY_HASH, hash);
  localStorage.setItem(KEY_ATTEMPTS, '0');
  notifyPinChanged();
}

/** Returns true if PIN matches. On match resets attempts. On mismatch
 * increments attempts; reaching MAX_ATTEMPTS wipes PIN (soft reset). */
export async function verifyPin(pin: string): Promise<boolean> {
  const salt = localStorage.getItem(KEY_SALT);
  const expected = localStorage.getItem(KEY_HASH);
  if (!salt || !expected) return false;
  const actual = await sha256Hex(salt + pin);
  if (actual === expected) {
    localStorage.setItem(KEY_ATTEMPTS, '0');
    return true;
  }
  const next = getAttempts() + 1;
  localStorage.setItem(KEY_ATTEMPTS, String(next));
  if (next >= MAX_ATTEMPTS) {
    clearPin();
  }
  return false;
}

export function clearPin(): void {
  localStorage.removeItem(KEY_HASH);
  localStorage.removeItem(KEY_SALT);
  localStorage.removeItem(KEY_ATTEMPTS);
  notifyPinChanged();
}

function notifyPinChanged(): void {
  window.dispatchEvent(new CustomEvent(EVT));
}

export function subscribePinChanged(cb: () => void): () => void {
  window.addEventListener(EVT, cb);
  return () => window.removeEventListener(EVT, cb);
}
