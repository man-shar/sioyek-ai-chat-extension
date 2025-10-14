# sioyek-ai

Python helper that streams highlighted passages from sioyek to OpenAI, shows the reply in a PyQt window, stores the conversation in sioyek's databases, and marks the source text with a dedicated highlight colour.

### Database changes (heads-up)

- The first run calls `ALTER TABLE highlights ADD COLUMN is_ai INTEGER DEFAULT 0` on `shared.db`. Back up your Sioyek databases if you need to preserve their pristine schema.
- Every AI-assisted highlight writes to the existing `highlights` table: reused highlights get `is_ai = 1`, and new ones are created with type `v`. Manual highlights stay untouched unless you later reuse them for AI.

## Features

- Streams OpenAI answers into a PyQt dialog while you keep reading in sioyek.
- Reuses or creates a purple (`type = v`) highlight for the selection and flags it with `highlights.is_ai = 1`.
- Saves every exchange—question, streamed answer, context snippet, and metadata—into `ai_sessions` / `ai_messages`.
- Pulls extra context with PyMuPDF: nearby page text, detected Abstract, file name, and document title.
- Keeps a per-document history sidebar so you can jump between past sessions (even mid-stream).
- Writes detailed diagnostics and tracebacks to `logs.txt` and mirrors key events in the sioyek status bar.

## Setup

1. Ensure [uv](https://github.com/astral-sh/uv) is installed (already used in this repo).
2. Create/refresh the virtual environment and install dependencies:
   ```bash
   UV_CACHE_DIR="$PWD/.uv-cache" uv sync
   ```
3. Copy `.env` to fill in your OpenAI credentials (the file is created with empty defaults):
   ```bash
   echo "OPENAI_API_KEY=sk-..." >> .env
   # optional overrides:
   # OPENAI_MODEL=gpt-4o-mini
   ```

> **Heads-up:** the command now uses PyQt5 to show results, so it needs a graphical session (X11/Wayland) when you trigger `_ask_ai`.

You can activate the environment with `source .venv/bin/activate` if you prefer.

## Sioyek integration

Add the helper commands to `prefs_user.config` (adjust paths if you clone somewhere else):

```text
new_command _ask_ai /home/manshar/projects/sioyek-ai/.venv/bin/python /home/manshar/projects/sioyek-ai/sioyek_ai/ask_ai.py "%{sioyek_path}" "%{selected_text}" "%{file_path}" "%{command_text}" "%{selection_begin_document}" "%{selection_end_document}" "%{local_database}" "%{shared_database}"

new_command _show_ai_history /home/manshar/projects/sioyek-ai/.venv/bin/python /home/manshar/projects/sioyek-ai/sioyek_ai/show_history.py "%{sioyek_path}" "%{file_path}" "%{mouse_pos_document}" "%{local_database}" "%{shared_database}"

shift_click_command _show_ai_history
```

Bind `_ask_ai` to a key in `keys_user.config`, for example:

```text
_ask_ai <C-a>
```

### `_ask_ai` (selection → answer)

Selecting text and running `_ask_ai`:

- loads `.env` (via `python-dotenv`) and reads `OPENAI_API_KEY` plus optional tuning vars,
- reuses an existing AI highlight near the selection or creates a new purple (type `v`) one and flags it in `shared.db` (`highlights.is_ai = 1`) so you can spot AI-assisted passages later,
- gathers nearby context from the page and basic metadata (file name, PDF title, detected abstract if any) and sends that alongside the selected text,
- inserts a new row into `shared.db` (`ai_sessions` + `ai_messages`) tied to the highlight and document hash,
- opens a PyQt window immediately (even while the model is thinking) so you can see the document path, highlighted text, question, and the answer streaming in live,
- keeps the history sidebar in sync so selecting an entry swaps in that saved conversation, and
- appends detailed logs—including stdout/stderr—to `logs.txt` in this directory while emitting status messages inside sioyek.

Trigger `_ask_ai` with an empty question to open the history window without calling the API—the current selection (and any AI highlight detected nearby) will be preloaded so you can browse past chats.

If no text is selected or the API call fails, the status bar and `logs.txt` include the relevant error.

### `_show_ai_history` (Shift-click shortcut)

Assigning `shift_click_command _show_ai_history` lets you Shift-click anywhere in the document to pop open the history dialog for the closest AI highlight. The script uses `%{mouse_pos_document}` to match highlights, and only highlights tagged with `is_ai = 1` are considered—so manual highlights stay untouched.

## Local testing

Run the module directly (using `echo` as a harmless stand-in for `sioyek_path`). Provide some dummy coordinates and point to your actual sioyek databases:

```bash
.venv/bin/python -m sioyek_ai.ask_ai \
  $(which echo) \
  "Example selection" \
  "/path/to/doc.pdf" \
  "Why is this important?" \
  "0 100 200" \
  "0 150 220" \
  "$HOME/.local/share/Sioyek/local.db" \
  "$HOME/.local/share/Sioyek/shared.db"
```

With an empty `OPENAI_API_KEY`, the command reports the missing key; once you add a valid key, it will reach the API. The question argument is optional—omit it or leave it blank to rely only on the highlighted text.
