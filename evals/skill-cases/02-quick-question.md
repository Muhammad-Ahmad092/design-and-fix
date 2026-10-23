# 02: Single focused question

## Prompt

```text
Should updating a user's email address be PUT or PATCH?
```

## Setup

- Any folder (a fresh copy of `tests/fixtures/monorepo` is fine), plugin installed, no `docs/` folder.

## Must

- [ ] Answered directly in chat, in a few lines
- [ ] Makes one pick (PATCH expected for a partial update) with a one-line reason
- [ ] No files written, no folders created

## Must not

- [ ] Runs `scan_repo.py`
- [ ] Asks the context questionnaire
- [ ] Creates `docs/architecture/` or any `quick-*.md` file
- [ ] Answers "it depends" without a pick
