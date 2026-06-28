# CLAUDE.md — Project Rules for AI Assistants

This file defines mandatory rules for any AI assistant working on this codebase.
Read and follow all rules before writing or modifying any code.

---

## Language

**All code must be written in English.**

This applies to:
- Variable names, function names, class names, and constants
- Code comments and inline documentation
- Docstrings and help text inside Python files
- Template text that is part of logic (e.g., error keys, status values, dict keys)
- Commit messages and PR descriptions

**Exception:** User-facing UI strings (labels, button text, error messages shown to end users)
may be in Indonesian if the product language is Indonesian. However, the surrounding code
structure, keys, and logic must still use English.

Do not mix languages within the same layer. If a comment block is in English, keep it in
English. Never switch to Indonesian mid-comment or mid-variable name.

---

## Code Style

- Follow PEP 8 for Python code.
- Use `snake_case` for variables and functions, `PascalCase` for classes.
- Keep functions focused and short. If a function does more than one thing, split it.
- Do not add error handling, fallbacks, or validation for scenarios that cannot happen.
  Trust internal code and framework guarantees. Only validate at system boundaries
  (user input, external APIs).
- Do not add features, refactor, or introduce abstractions beyond what the task requires.
  Three similar lines is better than a premature abstraction.

---

## Comments

- Write comments only when the **why** is non-obvious: a hidden constraint, a subtle
  invariant, a workaround for a specific bug, or behavior that would surprise a reader.
- Do not explain **what** the code does — well-named identifiers do that already.
- Do not reference the current task, ticket number, or calling context in comments.
  Those belong in commit messages, not in source code.
- Keep comments to one short line when possible. No multi-paragraph comment blocks.

---

## Security

- Never introduce SQL injection, XSS, command injection, or other OWASP Top 10
  vulnerabilities.
- Never commit secrets, credentials, or API keys.
- Always use Django's CSRF protection for state-changing views.
- Validate and sanitize all user-supplied input at the view/form boundary.

---

## Django Conventions

- Use `get_object_or_404` for user-facing lookups.
- Read model instance field values **before** calling `form.is_valid()`. Django's
  `_post_clean()` updates the bound instance in-place, so reading after validation
  gives the new (post-edit) value, not the original.
- Use `@require_POST` on views that perform state-changing operations.
- Keep business logic in views or service functions, not in templates.

---

## Templates

- All template logic variables, tag names, and filter names must be in English.
- User-facing text in templates may be in Indonesian (product language).
- Do not inline complex logic in templates. Move it to a view or template tag.

---

## Files to Never Commit

See `.gitignore`. In addition:
- Do not commit `seed_*.py` files — they contain demo data.
- Do not commit internal planning or proposal documents (added to `.gitignore`).
- Do not commit `.env` or any file containing real credentials.
