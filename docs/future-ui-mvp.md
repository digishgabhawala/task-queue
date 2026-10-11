# Future MVP UI backlog

Status: deferred until multi-user authentication is available

## Decision

The current admin UI foundation is sufficient for the single-user phase. The
items below are important, but implementing them before login, authorization,
and user management would create navigation and interaction patterns around an
unfinished access model.

Until that prerequisite is complete, UI work should be limited to regressions,
accessibility defects, and problems that block current queue operation. New UI
features belong in this future MVP backlog.

## Entry criteria

Resume this backlog after the application has:

- Login, logout, and durable session behavior.
- Protected admin and API routes.
- Defined user roles and authorization rules.
- A user-management model appropriate for the intended deployment.
- A decision about which operational data may be visible in a public portfolio.

## Planned increments

Complete these as small, independently reviewable pull requests.

### 1. Authenticated application shell

- [ ] Show the signed-in user and their effective role.
- [ ] Provide predictable account and logout navigation.
- [ ] Add permission-denied and expired-session states.
- [ ] Preserve the existing responsive and keyboard behavior.

### 2. Task discovery

- [ ] Filter recent tasks by status and task type.
- [ ] Add worker or identifier search if real usage demonstrates the need.
- [ ] Preserve filter state in the URL for refreshes and shared links.
- [ ] Provide clear-filter and useful no-results states.
- [ ] Announce updated result counts without moving keyboard focus.

The current `/api/tasks` endpoint already accepts `status` and `task_type`, so
the first filter increment should not require a new query contract.

### 3. Read-only task details

- [ ] Add a deep-linkable task details page or panel.
- [ ] Show the full task ID, worker, timestamps, status, and reported progress.
- [ ] Provide a copy-ID action with accessible success feedback.
- [ ] Define which payload, error, and artifact metadata each role may see.
- [ ] Avoid exposing prompts, provider errors, secrets, or internal stack traces.

### 4. Data scale and navigation

- [ ] Replace the fixed recent-task result with server-side pagination.
- [ ] Add sorting only for fields that support real operational decisions.
- [ ] Keep result counts and time ranges explicit.
- [ ] Verify performance with the expected worker and task volumes.
- [ ] Retain the labelled mobile-record layout without horizontal page scroll.

### 5. Portfolio-safe presentation

- [ ] Decide between an authenticated live view and a sanitized demo mode.
- [ ] Ensure public views never expose private task or worker information.
- [ ] Add a favicon and social-sharing metadata.
- [ ] Provide curated sample states that demonstrate loading, progress, success,
      and recovery without using production data.

### 6. Automated UI quality gate

- [ ] Add automated accessibility checks for the admin surface.
- [ ] Add keyboard and focus regression coverage for interactive workflows.
- [ ] Check 200% zoom and 320px reflow behavior.
- [ ] Add a lightweight performance budget for fonts, CSS, and JavaScript.
- [ ] Keep the existing shared-token validator in the required checks.

### 7. AI Bharatiyaa suite adoption

- [ ] Apply the Commons foundation to a second application.
- [ ] Compare the two implementations before extracting shared patterns.
- [ ] Introduce cross-product navigation only when at least two products need it.
- [ ] Promote components or an AI Bharatiyaa UI skill only after repeated use
      establishes their API, responsive behavior, and accessibility states.

## Explicitly later

These are not part of the first multi-user UI MVP:

- Dark mode, until it has a demonstrated usage need and both themes can be
  tested together.
- A shared component package or framework-specific design-system package.
- Charts without useful historical data and a stated operational question.
- Worker controls such as pause, drain, retry, or shutdown before backend
  permissions and safe command behavior exist.
- Decorative cultural themes. Bharatiya identity should continue to come from
  real language, workflows, content, and typography.

## Definition of done

Each resumed increment must use the pinned Commons tokens, cover loading,
empty, success, stale, error, and permission implications, and be checked at
375px, 768px, 1024px, and 1440px. Deferred work should return to this document
instead of silently expanding a pull request.
