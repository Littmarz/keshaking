# frontend/ — React + Vite + TypeScript

**Владелец:** 🧭 Капитан · **Подробный план:** [../docs/team/1-captain-backend-frontend.md §6](../docs/team/1-captain-backend-frontend.md#6-frontend-структура-экраны-компоненты)

Экраны: **Вход** (мок-пользователи) · **Чат** (шаги агента, ответ, цитаты, панель документа) · **Документы** (статусы редакций) · **Аналитика** (KPI, неудачные поиски, пробелы в базе).

```
src/
├── api/          client · auth · chat · stream (SSE) · documents · analytics · feedback
├── types/        api.ts — TS-копия docs/02-contracts.md
├── store/        authStore · uiStore (zustand)
├── hooks/        useAuth · useChatStream · useDocument
├── pages/        Login · Chat · Documents · DocumentView · Analytics
├── components/
│   ├── layout/     AppShell · Header · UserSwitcher
│   ├── chat/       SessionSidebar · MessageList · AnswerCard · ConflictCard · RefusalCard
│   │               CitationChip · AgentStepsTimeline · FeedbackButtons · ChatInput · ExampleQuestions
│   ├── documents/  DocumentTable · DocStatusBadge · DocumentViewer · SectionToc · VersionHistory · DocumentSheet
│   ├── analytics/  KpiTiles · QueriesTimelineChart · StatusPieChart · FailedQueriesTable · GapsList · TopicsChart
│   └── ui/         shadcn
└── lib/          format · citations
```

## Запуск (после 26.11)

```bash
pnpm i
pnpm dev     # http://localhost:5173, /api проксируется на :8000
```
