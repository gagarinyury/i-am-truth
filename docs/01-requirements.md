# Требования хакатона и чеклист подачи

> Источник: https://allthingsagentichackathon.devpost.com/ и .../rules
> Снято 2026-08-27 через WebFetch. **Перепроверить за сутки до подачи** — правила
> на Devpost правятся организаторами без уведомления.

## Дедлайн

**31.08.2026, 17:00 PDT** = **01.09.2026, 02:00 CEST (Ницца)**.
Призовой фонд **$180 000** (в первой версии STATE.md стояло $190k — ошибка, исправлено
по первоисточнику).

## Обязательный стек — жёсткие требования

| # | Требование (цитата) | Статус | Чем закрываем |
|---|---|---|---|
| R1 | «Gemini **3.5 or newer** accessed through Gemini API or Vertex AI» | ✅ **технически закрыто** | `gemini-3.7-flash` (Vertex, global) отвечает — F-01, F-02, F-09. Остаётся Q-00: держит ли качество |
| R2 | «at least one Google Agent Framework: Google ADK, GenAI SDK, Antigravity SDK or GenKit» | ✅ **закрыто минимально** | `google-genai` 1.56.0 работает с Vertex (F-09). ADK — апгрейд ради 30% за архитектуру, не необходимость |
| R3 | «at least one Google Cloud infrastructure service (Cloud Run, Cloud SQL, Firestore, GKE, Pub/Sub)» | ✅ **закрыто** | Cloud Run, сервис `i-am-truth` развёрнут и отвечает (F-36) |
| R4 | «Must demonstrate the backend is running on Google Cloud» (Cloud Console, Cloud Run dashboard, Vertex AI logs, URL вида `.run`) | ✅ **закрыто** | `https://i-am-truth-242136767009.us-central1.run.app` (F-36); в видео показать дашборд |
| R5 | «a next-generation, autonomous AI Agent leveraging Gemini 3.5 that operates beyond standard chat loops» | ✅ **закрыто** | Cloud Run Job `i-am-truth-batch`: находит корпус по запросу Europe PMC, разбирает параллельно, складывает в GCS. Прогон в облаке выполнен (F-37) |

⚠️ **R1 — самая опасная строка.** Проверенный результат проекта (3.5/5 → 4.5/5)
получен на Gemini **2.5 Pro**, которая порог не проходит. Pro-линейка на Vertex
обрывается на `gemini-3.1-pro-preview`. Доступны ≥3.5 **только Flash-модели**.

## Артефакты подачи

| # | Артефакт | Требование | Статус |
|---|---|---|---|
| S1 | Демо-видео | **≤4 мин**, публично на YouTube или Vimeo, английский или англ. субтитры | 🔲 |
| S2 | Репозиторий | GitHub / GitLab / Bitbucket (публичный или приватный) | 🔲 |
| S3 | README со spin-up-инструкцией | обязательно | 🔲 |
| S4 | **Архитектурная диаграмма** | «system visualization» | 🔲 |
| S5 | Текстовое описание | features, technologies, data sources, learnings | 🔲 |
| S6 | Hosted project URL | «encouraged but not strictly required for judging» | ✅ `https://i-am-truth-242136767009.us-central1.run.app` |
| S7 | Пруф деплоя на Google Cloud | в видео и/или репо (= R4) | 🟡 сервис, job и бакет есть — снять на видео |

## Правило новизны кода

> «Projects must be newly created during the Submission Period. Participants may use
> standard development tools, including frameworks, libraries, starter templates, and
> AI coding assistants, but must **disclose any other pre-existing code or work**
> incorporated into the Project.»

Период подачи открылся 03.08.2026. Практический вывод:

- Код пишем с нуля в этом репозитории — история git служит доказательством.
- Корпус разборов из `~/code/my-research/` — это **данные и эталон**, не код.
  Если оттуда переносится текст/числа в репозиторий — **декларировать** в описании
  (раздел «data sources» + отдельная строка про pre-existing work).
- Результаты замеров, снятые до 03.08, не использовать как «сделано в рамках проекта».

## Критерии судейства

| Вес | Критерий | Что это значит для нас |
|---|---|---|
| **40%** | Innovation & Operational Utility | Наш козырь — moat в retrieval, а не в промпте. Нужна демонстрация реальной пользы, а не «ещё один чат». |
| **30%** | Architectural Discipline & Tech Stack | Закрывает открытый вопрос «нужен ли ADK» — **нужен**, это треть оценки. |
| **30%** | Demo & Production Readiness | Отсюда следует обязательность деплоя, а не локального прогона. |

## Исключённые территории

Italy, Quebec, Crimea, Cuba, Iran, Syria, North Korea, Sudan, **Belarus, Russia**.

Резидентство участника — Франция (Nice) → проходит. Оценка идёт по стране проживания,
не по стране происхождения.

## Что ещё не выяснено про правила

- Точная формулировка про командность/индивидуальную номинацию (есть приз
  «Individual/Hobbyist», $10k × 2) — проверить, нужна ли отдельная отметка при подаче.
- Требуется ли регистрация на Devpost заранее или достаточно подать проект до дедлайна.
- Есть ли обязательная форма/шаблон для architecture diagram.
