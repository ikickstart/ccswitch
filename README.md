# ccswitch

Switch the Claude Code login between your own accounts. No browser, no restart,
every open session follows, every conversation keeps its context.

```
$ ccswitch list
  account                       plan   5h                      7d                      saved (UTC)
* manish@example.com            team   88% until Sun 16:49     21% until Mon 22:29     2026-10-19 11:02
  second@example.com            max    0% (reset)              35% until Tue 09:00     2026-10-18 20:11

$ ccswitch second
switch manish@example.com -> second@example.com  [max / Second org]
  slot saved 2026-10-18T20:11:40+00:00; last seen five hour 0% (resets Sat 09:00), seven day 35% (resets Tue 09:00)
proceed? [y/N] y
now on second@example.com. Open sessions follow within ~30s.
```

macOS only. Python 3.9+, standard library only, nothing to install beyond the
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
git clone https://github.com/<you>/ccswitch
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

## Use

```sh
ccswitch list               # slots and their last known usage
ccswitch status             # who is logged in now
ccswitch second             # switch; an unambiguous prefix is enough
ccswitch second -y          # without the question
ccswitch second --probe     # also send one tiny request to prove the token works
ccswitch doctor             # check the live item, the index and every slot
ccswitch remove old@x.com   # forget a slot (the account itself is untouched)
```

Watch the usage bar in your status line (for example
[claude-hud](https://github.com/jarrodwatts/claude-hud)); when it nears the
limit, switch from any shell. Sessions already open pick the new account up
within half a minute. `ccswitch list` shows the usage each slot had when it was
last saved, so you can pick the one with headroom; a window past its reset time
shows as empty.

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
