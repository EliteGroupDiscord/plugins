# Modmail Backup

Portable backup/restore plugin for modmail-dev/Modmail v4.x.

## Backed up
- snippets and aliases
- Modmail settings
- command/level permissions
- auto triggers and block lists
- installed plugin list
- best-effort plugin database partitions

## Never backed up
- Discord bot token
- MongoDB/connection URI
- GitHub token/passwords
- deployment-specific guild IDs and log URL
- thread logs (this is a portable configuration backup, not a full MongoDB dump)

## Commands
- `?backup create`
- `?backup create core`
- `?backup inspect` (attach backup JSON)
- `?backup restore` (attach backup JSON)
- `?backup restore core` (attach backup JSON)

Every restore automatically sends a pre-restore safety backup first.

## Install
Upload the `modmailbackup` folder to your GitHub plugin repository.

Typical current syntax:
`?plugin add YOUR_USERNAME/YOUR_REPO/modmailbackup@main`

If your build uses another form, run `?help plugin`.

## Migration
1. Install this plugin on the OLD hosted bot.
2. Run `?backup create`.
3. Download and keep the JSON attachment.
4. Set up your new Modmail with its own environment variables.
5. Install this plugin there.
6. Attach the JSON to `?backup restore`.
7. Restart the bot.

Third-party plugin data is best-effort because a plugin may use external storage or IDs
specific to the old server.
