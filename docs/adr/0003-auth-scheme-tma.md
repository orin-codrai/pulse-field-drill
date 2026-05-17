# ADR 0003 — Auth-схема: `Authorization: tma <initDataRaw>`

- **Status:** Accepted
- **Date:** 2026-05-17
- **Deciders:** @orrin

## Контекст

Backend валидирует Telegram Mini App `initData` на каждом запросе. Нужно
выбрать способ доставки `initDataRaw` от фронта к бэку — header, cookie
или request body.

## Рассмотренные варианты

### `Authorization: tma <initDataRaw>` ✅ выбрано

- Семантически правильно: `Authorization` — стандартный header для аутентификации.
- Прокси (Caddy) и фреймворки (FastAPI `Header(...)`) понимают его из коробки.
- Не попадает в HTTP-кеши, в URL-логи, в `Referer`.
- Схема `tma` короткая, читаемая в логах.

### `X-Telegram-Init-Data: <initDataRaw>` (custom header)

- Семантически менее правильно — это аутентификация, а не «доп. метаданные».
- Не используется в популярных libs; кастомный namespace.

### Cookie

- Браузер шлёт автоматически — удобно для SPA.
- Но требует SameSite/Secure-настройки, теряется при cross-origin, плохо ложится
  на Telegram WebView. Лишний слой сложности без выгоды для нашего кейса.

### Body (POST с initData в JSON)

- Ломает RESTful design: GET-запросы не могут иметь body.

## Решение

`Authorization: tma <initDataRaw>` на всех запросах к `/api/*`. Схема
`tma` — **наш выбор для проекта**, не общеотраслевая конвенция: в
aiogram / telegram-apps / python-telegram-bot единого консенсуса нет,
встречаются `tma`, `TWA`-prefix, кастомные headers.

## Последствия

- Frontend: `apiFetch(path, raw, init?)` добавляет header автоматически.
- Backend: FastAPI dependency `current_user(authorization: str = Header(...))`
  парсит схему, валидирует raw часть через HMAC-SHA256, возвращает `TelegramUser`.
- При переезде паттерна в Pulse — оставляем то же имя схемы. Если Pulse
  обзаведётся другими типами клиентов (внешний CRM, OAuth-сервисы) — для них
  будут отдельные схемы (`Bearer`, etc.) в том же header.
