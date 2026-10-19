# ccswitch

Switch the Claude Code login between your own accounts. No browser, no restart,
every open session follows, every conversation keeps its context.

```
$ ccswitch
     account                       plan   5h                      7d                      saved (UTC)
*  1 manish@example.com            team   88% until Sun 16:49     21% until Mon 22:29     2026-10-19 11:02
   2 second@example.com            max    0% (reset)              35% until Tue 09:00     2026-10-18 20:11

$ ccswitch second
now on second@example.com (was manish@example.com); open sessions follow within ~30s
```

macOS only. Python 3.10+, standard library only, nothing to install beyond the
script.

## Why it works

Claude Code keeps one login per config directory, in the macOS Keychain item
`Claude Code-credentials`, and every running session re-reads that item on a
30-second cache. That is why `/login` in one terminal switches every session on
the machine. `ccswitch` does what `/login` does to that item, from a saved copy
instead of a browser.

- `ccswitch save` snapshots the live item into a Keychain slot keyed by the
  account email, together with the account label and the usage snapshot that
  Claude Code keeps in `~/.claude.json`.
- `ccswitch <email>` saves the outgoing login first, then loads the chosen slot
  into the live item and relabels `~/.claude.json`. Only the `claudeAiOauth`
  block moves; everything else in both places stays.
- Slots are Keychain items under the service `ccswitch`, written through
  `security -i` so a token never appears in `ps`. The index at
  `~/.config/ccswitch/slots.json` holds emails, plan, org, save time and the
  usage snapshot, never a token.

Session transcripts live under the config directory keyed by project folder,
not by account, so they are untouched by a switch. The account named in the
status line may lag for a moment; the token is what is billed.

## Install

```sh
git clone https://github.com/ikickstart/ccswitch
cd ccswitch && make install        # symlinks ./ccswitch into ~/.local/bin
```

Or copy `ccswitch` anywhere on your PATH.

## Set up, once per account

```sh
ccswitch save                                        # the account logged in now
ccswitch login second@example.com                    # the real browser login, then save
ccswitch login third@example.com
ccswitch list
```

`ccswitch login` is just `claude auth login --email <addr>` followed by
`ccswitch save`. The browser opens once per account and never again, unless a
slot dies (below).

**The browser decides which account you get.** The authorise page approves
whichever account claude.ai is signed in as in that browser; the email on the
command line is only a hint on the sign-in page. So between two logins, sign
out of claude.ai in the browser (or use a private window) and sign in as the
next account. Signing out of the website does not touch any Claude Code login.
If the browser was signed in as someone else, `ccswitch login` says so, saves
the slot under the account it actually got, and exits 3.

Run the logins in a normal terminal; a switch works from anywhere, including
the `!` prefix inside a Claude Code session.

## Use

```sh
ccswitch                    # the slots, numbered, with their last known usage
ccswitch status             # who is logged in now
ccswitch second             # switch: a unique prefix, the full email, or the number from the list
ccswitch 2                  # same thing by number
ccswitch alias work second  # name a slot (email, prefix or number); run again to move or rename it
ccswitch work               # switch by alias
ccswitch alias              # list aliases; `ccswitch unalias work` drops one
ccswitch next               # the saved account with the most headroom
ccswitch second --ask       # show the slot and ask first (a switch never asks otherwise)
ccswitch second --probe     # also send one tiny request to prove the token works
ccswitch doctor             # check the live item, the index and every slot
ccswitch daemon install --switch-at 90 --compact-at 80   # see below
ccswitch remove old@x.com   # forget a slot (the account itself is untouched)
```

Watch the usage bar in your status line (for example
[claude-hud](https://github.com/jarrodwatts/claude-hud)); when it nears the
limit, switch from any shell. Sessions already open pick the new account up
within half a minute. `ccswitch list` shows the usage each slot had when it was
last saved, so you can pick the one with headroom; a window past its reset time
shows as empty.

## The daemon: switch and compact automatically

```sh
ccswitch daemon install --switch-at 90 --compact-at 80
ccswitch daemon status
ccswitch daemon log
ccswitch daemon uninstall
```

`install` does three things and `uninstall` undoes all of them:

1. **Puts a tee in front of your status line.** Claude Code hands its status-line
   command, every ten seconds per session, the five-hour and weekly usage, the
   context percentage, the session id and the transcript path. The tee records
   that to `~/.config/ccswitch/sessions/` and runs your original command
   unchanged (claude-hud keeps working). No token, no API call: the feed is what
   Claude Code already computes.
2. **Sets Claude Code's own compaction threshold.** `--compact-at 80` writes
   `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80` into the `env` block of your settings, so
   every session started afterwards compacts itself at 80% context.
3. **Registers `ccswitch watch` with launchd**, started at login, restarted if it
   dies, logging to `~/Library/Logs/ccswitch.log`. It checks usage and context
   every 5 minutes by default; the feed under it updates every 10 seconds.

The loop, every 5 minutes (`--interval`, in seconds, changes it):

- A session whose context is at or above `--compact-at` is asked to compact
  through the runtime methods in `--compact-via` (default `native,cmux`):
  `cmux` types `/compact` into that session's cmux pane (the pane is known
  because the tee records the session's `CMUX_SURFACE_ID`), and `cmd:<command>`
  runs your own command with the session's JSON on stdin, for any other
  terminal. Each session is compacted at most once per `--compact-cooldown`.
  cmux refuses to type into a pane where you are mid-sentence or a dialog is
  open; `--force` overrides that.
- When the five-hour window reaches `--switch-at` (or the weekly window reaches
  `--weekly-at`, if given), the daemon compacts every live session, waits until
  each transcript records the compaction (or `--compact-timeout` passes), then
  loads the saved account with the most headroom: lowest known five-hour usage,
  a window past its reset counting as empty, dead slots skipped for the next
  candidate. A macOS notification names both accounts. Open sessions follow
  within half a minute. `--cooldown` (default 300s) stops it flapping.
- If every saved account is at or above the threshold it notifies and waits.

`ccswitch watch --once --dry-run -v` shows what the loop would do right now
without changing anything. Thresholds and methods are given at install time
and live in the launchd plist; re-run `install` to change them.

**What a switch does to a running session: nothing.** The account is a Keychain
item each session re-reads on its next request. Context, transcript and running
subagents are untouched. The compaction before a switch is a choice, not a
necessity: it keeps every session small at the moment it moves to the other
account's window.

## Rules

1. **Never `/logout`.** It revokes the token you are leaving. `/login` and
   `ccswitch` only replace it.
2. **Never `/login` inside a session once set up.** It leaves the slot stale.
   Log in only through `ccswitch login <email>`, which saves afterwards.
3. **If a slot dies, log in once:**
   ```sh
   claude auth login --email <addr> && ccswitch save
   ```
   `ccswitch` prints exactly that line whenever it finds a dead slot.

## When a slot dies

Refresh tokens rotate on use and carry an expiry (about three and a half weeks
from issue, on the tokens seen so far; `ccswitch doctor` and the index show the
date). A slot dies when its refresh
token expired, when it was used by another copy of the credential after the
slot was saved, or in one rare race: a session was refreshing the outgoing
account's token at the exact moment of a switch. Claude Code then drops the
rotated token (its own write guard), so the slot keeps the old one, which the
server has since retired. Every case ends the same way: one browser login for
that account, then `ccswitch save`. Nothing else is affected.

`ccswitch doctor` shows slots that are already known to be dead (blanked or
expired). A slot the server has retired looks healthy until it is used; `--probe`
on a switch catches that at the cost of one tiny request.

## Caveats, stated plainly

- **Undocumented internals.** The Keychain item name, the JSON shape and the
  30-second re-read were read from the Claude Code binary, not from
  documentation. The script checks the shape before every write and refuses
  anything it does not recognise, so a Claude Code update breaks it loudly,
  never silently. Run `ccswitch doctor` after an update.
- **Terms.** Anthropic's consumer terms prohibit circumventing usage limits and
  sharing accounts. Several paid accounts each used within its own limit is a
  grey area; an account another person also uses is account sharing outright.
  Read the terms and decide for yourself. This tool does not hide anything from
  Anthropic: every token it stores was issued by a normal login.
- **Label lag.** A running session may rewrite `~/.claude.json` from memory and
  put the old email back for a while. Cosmetic.
- **One config directory.** `CLAUDE_CONFIG_DIR` is honoured; slots are global
  across directories, which is what you want.

## Development

```sh
make test     # unit tests against a fake `security` and a fake `claude`; the real Keychain is never touched
```

## Licence

MIT.
