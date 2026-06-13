# Summary

Session resume:

- by name
- by id

Session naming:

- Auto naming
- or Let user define themselves

## **Setting a Title Manually**

Use the `/title` slash command inside any chat session (CLI or gateway):

```
/title my research project
```

The title is applied immediately. If the session hasn't been created in the database yet (e.g., you run `/title` before sending your first message), it's queued and applied once the session starts.

You can also rename existing sessions from the command line:

```bash
hermes sessions rename 20250305_091523_a1b2c3d4 "refactoring auth module"
```

## **Title Rules**

- **Unique** — no two sessions can share the same title
- **Max 100 characters** — keeps listing output clean
- **Sanitized** — control characters, zero-width chars, and RTL overrides are stripped automatically
- **Normal Unicode is fine** — emoji, CJK, accented characters all work

## **Auto-Lineage on Compression**

When a session's context is compressed (manually via `/compress` or automatically), Hermes creates a new continuation session. If the original had a title, the new session automatically gets a numbered title:

```
"my project" → "my project #2" → "my project #3"
```

When you resume by name (`hermes -c "my project"`), it automatically picks the most recent session in the lineage.

## **/title in Messaging Platforms**

The `/title` command works in all gateway platforms (Telegram, Discord, Slack, WhatsApp):

- `/title My Research` — set the session title
- `/title` — show the current title

## **Session Management Commands**

Hermes provides a full set of session management commands via `hermes sessions`:

## **List Sessions**

```bash
# List recent sessions (default: last 20)
hermes sessions list
# Filter by platform
hermes sessions list--source telegram
# Show more sessions
hermes sessions list--limit50
```

When sessions have titles, the output shows titles, previews, and relative timestamps:

```
Title                  Preview                                  Last Active   ID────────────────────────────────────────────────────────────────────────────────────────────────refactoring auth       Help me refactor the auth module please   2h ago        20250305_091523_amy project #3          Can you check the test failures?          yesterday     20250304_143022_e—                      What's the weather in Las Vegas?          3d ago        20250303_101500_f
```

When no sessions have titles, a simpler format is used:

```
Preview                                            Last Active   Src    ID──────────────────────────────────────────────────────────────────────────────────────Help me refactor the auth module please             2h ago        cli    20250305_091523_aWhat's the weather in Las Vegas?                    3d ago        tele   20250303_101500_f
```

## **Export Sessions**

```bash
# Export all sessions to a JSONL file
hermes sessionsexport backup.jsonl
# Export sessions from a specific platform
hermes sessionsexport telegram-history.jsonl--source telegram
# Export a single session
hermes sessionsexport session.jsonl --session-id 20250305_091523_a1b2c3d4
```

Exported files contain one JSON object per line with full session metadata and all messages.

## **Delete a Session**

```bash
# Delete a specific session (with confirmation)hermes sessions delete 20250305_091523_a1b2c3d4# Delete without confirmationhermes sessions delete 20250305_091523_a1b2c3d4--yes
```

## **Rename a Session**

```bash
# Set or change a session's titlehermes sessionsrename 20250305_091523_a1b2c3d4"debugging auth flow"# Multi-word titles don't need quotes in the CLIhermes sessionsrename 20250305_091523_a1b2c3d4 debugging auth flow
```

If the title is already in use by another session, an error is shown.

## **Prune Old Sessions**

```bash
# Delete ended sessions older than 90 days (default)
hermes sessions prune
# Custom age threshold
hermes sessions prune --older-than30
# Only prune sessions from a specific platform
hermes sessions prune--source telegram --older-than60
# Skip confirmation
hermes sessions prune --older-than30--yes
```
