# Alias Manager

Interactive multi-command aliases for `modmail-dev/Modmail` v4.x.

## Why

Kyb3r Modmail's built-in aliases can chain commands, but long aliases become
annoying to create because you have to manually write command separators.

Alias Manager stores each command as a separate step and gives you a
Discord button/modal editor.

## Install

Repository layout:

```text
plugins/
└── aliasmanager/
    ├── aliasmanager.py
    └── README.md
```

For the repository `EliteGroupDiscord/plugins` on the `main` branch:

```text
?plugin install EliteGroupDiscord/plugins/aliasmanager@main
```

Then restart or update/reload the plugin if your Modmail installation requests it.

## Commands

```text
?am create <name>
?am edit <name>
?am list
?am show <name>
?am copy <name> <new_name>
?am move <name> <from> <to>
?am delete <name>
```

Management commands require Modmail `OWNER` permission.

Once an alias is created, invoke it like any normal command:

```text
?closewarn
```

## Example

Create:

```text
?am create closewarn
```

Use **Add Step** three times:

```text
reply Please review the rules before contacting staff again.
note User was warned before closing.
close
```

Save it, then staff can run:

```text
?closewarn
```

## Variables

Steps support these replacements:

```text
{user}
{userid}
{moderator}
{moderatorid}
{channel}
{server}
{serverid}
```

Example:

```text
note {moderator} escalated {user} ({userid})
```

## Notes

* Commands execute in order.
* Each underlying Modmail command keeps its normal checks and permission rules.
* Alias names cannot conflict with a registered bot command.
* Enhanced aliases are intentionally separate from the built-in `?alias`
  command so the plugin does not replace or break core Modmail functionality.
* The alias runner is limited to the central Modmail/staff guild.
