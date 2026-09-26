# Background test sessions

`tools/game_session.py` prepares the selected local character in town through
AFK IPC. It uses the game's named menu/room-transition routines and reads JSON
state; it does not send mouse/keyboard input or directly edit saves.
This developer helper removes the repeated manual startup/character-selection
step. It does not automate combat or verify the mathematical combat model.

## Usage

In 0.3.0 the research world/combat telemetry is removed (`world: null`). Fresh
state retains identity, menu readiness, native hook status, capture status and
`farm_context`. Ready checks use two request-bound session states and no longer
require the archived combat snapshot command. Historical research receipts are
in the external archive and are not a dependency of the product.

From the AFK module directory, with Python 3.13 and no third-party packages:

```powershell
py -3 -B tools/game_session.py prepare
py -3 -B tools/game_session.py travel --room Act_01_01
py -3 -B tools/game_session.py travel --room Town_03_rm
py -3 -B tools/game_session.py status
py -3 -B tools/game_session.py close
```

The helper needs the Python `hs_game_sdk` package. It looks in `HS_GAME_SDK` (an
`hs-game-sdk` checkout or its `python` folder) first, then in the `hs-game-sdk`
checkout beside this repository (the hub layout; the release zip ships the same
folder), then in an installed package or `PYTHONPATH`. When none has it, the helper
stops with one message naming these three ways. If `HS_GAME_SDK` is set but holds no
package, the helper stops rather than trying the other places.

The current test target defaults to Suh, saved slot **2** (zero-based), class 8.
The game directory comes from the AFK config or `HS_GAME_BIN`; an explicit
`--game-bin` overrides it. Other targets require all three identity fields:

```powershell
py -3 -B tools/game_session.py prepare --slot 2 --name Suh --class-id 8
```

The helper requests a minimized launch without activation, and it gives the game
the system's default error mode (`CREATE_DEFAULT_ERROR_MODE`). A child process
otherwise inherits its parent's error mode. Git Bash runs with error mode 0x3,
which includes `SEM_NOGPFAULTERRORBOX`, so a game started from it crashed without
a WER report or a dump in `%LOCALAPPDATA%\CrashDumps`, even when the crash came at
close. A game started some other way keeps the error mode its launcher gave it.
The game can still create its own windows; this is background control, not a
headless game build.
When ready, `prepare` exits and leaves the game waiting in town. Calling it again
checks the existing character and takes a fresh snapshot without repeating menu
actions. `close` requests normal window closure, verifies process exit and
reports the game's exit code. It never force-terminates a process. The result and
the receipt carry these fields:

- `exit_code`: the exit code, unsigned.
- `exit_code_hex`: the same code in hex.
- `clean_exit`: `true` for exit code 0.

A nonzero code also gets an `exit_status` name and a warning on stderr. For example,
`0xC0000409` (3221226505) is `STATUS_STACK_BUFFER_OVERRUN`: a fast fail, such as an
exit-time `abort()`. The command still exits 0, because the close itself worked;
the crash is a finding about the game or a mod. The exit code is `null` when the
game was already closed. It is also `null` when Windows gave the helper no handle
to the process; the helper never reports a guessed 0. Judge a close by its exit
code, not by a missing dump. After a test, close the game when it is no longer
needed, as requested by the user.

`travel --room <SDK-room-name>` uses the existing `afk goto` command, which calls
the game's `RoomGoto` with the controller instance. It requires an already
loaded matching offline character and no active replay. The destination must
be an SDK-listed town or normal `Act_XX_YY` room. It sends the transition once,
waits for two matching destination states, then verifies a fresh native snapshot.
A request for the current room takes a new snapshot without reloading the room.
The 0.4.0 panel's **Restart region** therefore travels through the corresponding
town before returning to the original Act. Loading produces `farm_pause` and
`farm_resume` records in the same capture. It does not reset the sample, count
loading as farm time, or turn a temporary missing player into a new character.
The restart receipt (`verification/release-0.4.0/room-restart-check-2.json`) is kept
outside the public repository.
The game must already be running; `prepare` remains the startup command.

This is developer test travel, not proof that the player unlocked or cleared
the destination, and not evidence for expedition eligibility. Combat, boss,
special-dungeon and waypoint-unlock automation are outside this helper's scope.
Town exit, cross-act travel and return were verified along
`Town_03_rm → Act_01_01 → Act_03_04 → Town_03_rm` without desktop input.

## Contract

The plugin's read-only `afk session state <request-id>` command atomically writes
`%LOCALAPPDATA%/Hero_Siege/afk/models/session-state.json`. Schema 1 includes the
request ID, process ID, game build, plugin version, room, online/replay state,
menu/player instance counts, selected identity and visible character choices.
The helper requires a matching fresh request ID and process ID for each read.
Human command output is retained verbatim as diagnostics, never parsed into
readiness or character identity.

The observed transitions are:

1. `Menu_Controller_obj` → `UiAMainMenuLocal`.
2. The matching `Choose_Parent_obj` instance → `UiAChooseSaveSlot`.
3. `UI_Character_obj` → `UiACharacterPlay`.

All objects, scripts and rooms come from `hs-game-sdk`. The button's one-based
UI slot is converted to the zero-based saved slot. Its **instance ordinal** is
reported separately and must not be inferred from the saved slot. Before Play,
the selected slot, name and class must all match. Readiness requires two town
states followed by a new hash-checked native snapshot with matching identity.

Commands use the existing single-writer AFK IPC channel. Do not run other IPC
clients during preparation. A pending command is refused, not overwritten.
Each action is sent at most once per invocation; an uncertain result stops or
times out instead of repeatedly activating menu callbacks. Diagnostic receipts
are saved under `models/test-sessions/` on success and failure. A close receipt
keeps the exit code fields. A launch event records `default_error_mode`.

## Scope and verification

The first version is pinned to the locally measured 7.0.13.0 executable hash
and build. Executable changes require verification before updating that pin.
Unknown/online state, an active replay, multiple players, the wrong character,
unavailable/ambiguous choices or an unexpected menu stop preparation. `prepare`
refuses an already loaded character outside town; use explicit `travel` to
return from a supported act room. Character-page navigation is not implemented. The general
identity checks are unit tested; live startup was verified only for Suh on this
installation, not every character and game version.

Use this helper for setup instead of the legacy `dev_cycle.ps1 -Restart` path,
which uses older ForgePact/slot-setting assumptions. Run the fast regression
test without opening the game:

```powershell
py -3 -B -m unittest discover -s tests -p test_game_session.py -v
```

The launch and close tests start small `pythonw` stand-in processes, never the
game. `tests/close_stand_in.py` is an off-screen window that exits with a chosen
code when it is closed.

Historical startup and cross-act travel receipts are in the external research
archive under `verification/background-session-20260921` and
`verification/background-travel-20260921`. Current product evidence is the 0.4.0
report, also kept outside the public repository.

## Research call arguments

Backup plugin DLLs must stay outside `mods/aurie`: Aurie loads files ending in
`.dll` even when their names include `backup`. Preparation rejects multiple
similarly named AFK DLLs; live verification also checks process modules. IPC
waits out Windows output sharing violations without resending game commands.
These checks were added after the excluded setup attempts documented in the
external archive's `verification/damage-composition-20260921/REPORT.md`.

From `0.2.0-collision-context.1`, `builtinon`, `callon` and `callid` share
strict argument parsing. `me` means the unique live player; `self` means the
selected instance context. They also accept `undef`, finite complete numeric
tokens and apostrophe-prefixed strings. Invalid arguments and noninteger or
invalid selectors stop the target invocation. Previously `builtinon` turned
an unsupported `me` token into numeric zero; do not treat a successful command
reply as proof that a position changed. Always verify fresh JSON.

Historical argument and collision studies are kept in the external archive.
Those research commands are not part of the 0.4.0 player-facing feature set. The
general session helper provides setup/travel/closure, not an arbitrary-character
combat controller.
