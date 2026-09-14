# Yuki

A compact, modular Telegram group utility bot built with Kurigram and MongoDB. The structure intentionally stays close to the simple BioGuard layout: shared infrastructure is in `Client/`, while every bot feature is a module in `plugins/`.

## Structure

```text
Yuki/
├── Client/
│   ├── bot.py
│   ├── cache.py
│   ├── database.py
│   ├── helpers.py
│   └── premium.py
├── plugins/
│   ├── start.py
│   ├── help.py
│   ├── tag.py
│   ├── user.py
│   ├── welcome.py
│   └── updater.py
├── config.py
├── main.py
├── requirements.txt
├── setup.sh
└── README.md
```

## Included commands

### Tag System
- `/utag` — tag all members (admin only)
- `/tagall` — alias of `/utag` (admin only)
- `/atag` — tag administrators
- `/admin` — alias of `/atag`
- `/cancel` — cancel the active tag task (admin only)
- `/speed` — open the speed panel (admin only)
- `/speed 1` through `/speed 10` — custom interval
- `/speed reset` — restore 3 seconds

Presets: Turbo 1s, Fast 2s, Normal 3s, Slow 5s.

Tagging is isolated per group, prevents concurrent tag jobs in the same group, supports cancellation, persists speed in MongoDB, and handles Telegram FloodWait/RPC errors without taking down the bot.

### User Tools
- `/id` — own ID, or replied user's ID
- `/ping` — response latency
- `/afk [reason]` — set AFK status
- `/user_stats` — personal statistics

AFK is removed on the user's next normal activity, replies to replies/@mentions/text mentions, and uses a per-chat cooldown to prevent repeated AFK spam. Message and command statistics are database-backed.

### Welcome System
- `/setwelcome <text>`
- Reply to a text/photo/video with `/setwelcome`
- `/welcome on`
- `/welcome off`
- `/cleanwelcome on`
- `/cleanwelcome off`

Supported placeholders:
`{first_name}`, `{username}`, `{id}`, `{mention}`, `{title}`

Button syntax:
`[Rules](https://t.me/yourrules) | [Support](https://t.me/support)`

Text, photo and video welcomes are persisted per group. Clean welcome removes the previous welcome only after a new welcome has been successfully sent.

### Start / Help Center
`/start` follows the compact Telegram-native flow used by the reference bot: private chat gets the full menu, while a group start points users to private chat. The Help Center uses editable pages with colored buttons, Prev/page/Next navigation and Home. Current categories are only:

- Tag System
- User Tools
- Welcome System

### Updater
- `/update` — owner/sudo only
- GitHub latest commit check
- Confirmation UI
- Automatic backup
- Python syntax validation
- Requirements installation when dependencies change
- Exact file synchronization, including repository-side deletions
- Automatic rollback on update failure
- Manual rollback from the updater UI
- Update history
- Automatic owner notification when a new commit is detected
- Restart after successful update/rollback

Default repository: `TheArchon/Yuki`, branch `main`. Both are configurable through `.env`.

## Database

The database follows the centralized BioGuard-style MongoDB pattern using Motor. Yuki adds only the collections/state required by its requested features:

- `users`
- `groups`
- `afk`
- `welcomes`
- `stats`

Indexes are created at startup and user/group IDs are prefetched into memory caches in the same style as BioGuard.

## Premium custom emoji

All button icons and text custom-emoji wrappers are centralized in `Client/premium.py`. Replace the IDs there with the Telegram custom emoji IDs you want to use; no plugin needs to be edited for an ID change.

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in Telegram API credentials, bot token, owner ID and MongoDB URI.
3. Optionally configure support/updates URLs and the GitHub token.
4. Install dependencies:

```bash
bash setup.sh
```

5. Start:

```bash
source venv/bin/activate
python3 main.py
```

The updater never overwrites `.env`, the virtual environment, Git metadata, backups, or its own state file.
