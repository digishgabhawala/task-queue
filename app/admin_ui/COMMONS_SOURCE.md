# Commons source

`tokens.css` is vendored from `ai-bharatiya-commons` commit `741e8ce`
(`Build adoption-ready UI foundation v0.2`).

The file is copied rather than fetched at runtime so the deployed admin UI is
deterministic and does not depend on another repository being available.

When updating:

1. Review the Commons changelog and token migration notes.
2. Replace `tokens.css` with the selected pinned revision.
3. Update the commit above.
4. Run Task Queue's tests and visual checks at 375, 768, 1024, and 1440px.
