// Последний использованный счёт — client-side, чтобы дефолтить его в форме добавления
// (quick-add ≤3 тапа). Phase 4 сделает ключ workspace-scoped; сейчас один юзер.
const KEY = 'pulse:lastAccountId';

export function getLastAccountId(): number | null {
  try {
    const v = localStorage.getItem(KEY);
    const n = v ? Number(v) : NaN;
    return Number.isInteger(n) ? n : null;
  } catch {
    return null;
  }
}

export function setLastAccountId(id: number): void {
  try {
    localStorage.setItem(KEY, String(id));
  } catch {
    // приватный режим / storage отключён — дефолт просто не запомнится
  }
}
