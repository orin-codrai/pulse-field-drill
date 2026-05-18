/**
 * Форматирование денег + дат. Все суммы в БД хранятся в копейках (BIGINT).
 * Display — рубли через Intl.NumberFormat('ru-RU').
 */

const rubFormatter = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0, // целые рубли для основного UI; копейки скроем
});

const rubFormatterPrecise = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatRub(minor: number): string {
  return rubFormatter.format(minor / 100);
}

export function formatRubPrecise(minor: number): string {
  return rubFormatterPrecise.format(minor / 100);
}

/** "1234.56" → 123456 копеек. Только цифры + одна точка/запятая. */
export function parseRubInputToMinor(input: string): number | null {
  const normalized = input.trim().replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return null;
  const rub = parseFloat(normalized);
  return Math.round(rub * 100);
}

const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
});

const dateLongFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'long',
  year: 'numeric',
});

export function formatDate(iso: string): string {
  return dateFormatter.format(new Date(iso));
}

export function formatDateLong(iso: string): string {
  return dateLongFormatter.format(new Date(iso));
}

/** ISO date input value (YYYY-MM-DD) для <input type="date">. */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}
