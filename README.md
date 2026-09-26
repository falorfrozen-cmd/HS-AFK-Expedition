# AFK FARM · Hero Siege

A Windows companion that plans timed expeditions using your offline hero’s measured
farming pace. **0.7.0 — measured farming / beta release.** Kill rates come from your
calibration; the game generates loot when you claim your rewards.

## Download

**→ [Releases page](../../releases): download `AFK-FARM-<version>.zip`**, extract the
whole folder and open **AFK FARM.exe**. Python is bundled. Aurie and YYToolkit must
already be installed for your game version; `START_HERE.md` in the package walks
through the setup.

Start from a saved eligible profile with the game closed or another hero loaded.
The selected hero, live hero and past recording are shown separately. Changing
the selection does not change an active expedition.

Keep the recorded hero in its region while claiming. Reward delivery replays every
kill through the game, so it takes minutes to hours depending on the expedition;
Explore shows the time left as measured on your computer, with Normal, Fast and
Maximum speeds. **Pause and save delivery**, closing the game window or leaving the
region with the expedition hero saves and pauses it; claim again to continue.
**Claim in background** opens the game minimized, delivers and closes it again.
Only after delivery finishes are the game save and Vault transfer marked complete.

The Loot page has an expedition summary (rarities, best drops, XP, gold, level and
delivery time) and a **Vault transfer filter** that chooses which rarities, keys
and materials go to Infinite Vault; items left out stay in the records and
**Transfer again** adds them later. Explore offers **Farm again**, warns when the
loaded hero's loadout no longer matches a calibration, and can notify you when an
expedition is ready and when delivery finishes.

0.6.1 adds a **Region comparison** on Explore (each calibrated region's pace and XP
per hour next to the gold and rare drops per hour your delivered expeditions there
yielded), a **Windows notification** when an expedition is ready that also works
while AFK FARM and the game are closed, **Continue from recorded position** for a
delivery a crash cut short, and Vault category names with the hero and the time
actually claimed (`AFK · <date> · <hero> · <region> · <time>`).

0.6.2: **items your loot filter hides** can be converted in the game while
delivering: below Satanic they are sold for their own merchant value (gold to the
hero), Satanic and above are broken down with the game's Prospector recipes
(fragments to the Vault in stacks of up to 999). Selling runs only offline with no
connection to the Hero Siege servers. Choose it on the Loot page.

0.8 (in progress): a **camp** you build up for your workers (nine buildings, camp resources, the Siege gate's Walls), **traits**, **adventurers** (world chests with the game's keys), **goblin hunters**, a **jeweler** working the game's own jewel recipes, and **team trips** with synergies; the key rack and the jeweler's stock are filled from the Vault's AFK Materials (Item Editor 2.16.1 or newer), never twice; every item still comes from the game's own routines.

0.9 turns the camp into a **town**: walls and eight kinds of towers, **sieges** that bring the monsters you recorded (with their real affixes, plus the Abyss, Unholy Siege and Chaos Pillar packs) against it with stationed heroes, travelling **merchants** selling what the game's vendors never do, and **trade wagons** to ten towns. The town keeps its own coffer; every item it earns is still made by the game.

0.7.0: **several heroes at once** (one expedition per hero), **Siege** (hold a
calibrated region against growing waves: elite, treasure-goblin and boss waves, a
Magic Find bonus per level, records), a **collection** of all 916 uniques and set
items with a **wishlist** notification and a **share card**, verified **boss
kills** in calibrations, and **workers**: miners hired with gold that level up,
learn a skill tree and bring ore and materials from their own trips. The panel UI
for these is the next step; the local API is in docs/UI_CONTRACT.md.

0.6.5: a refreshed panel look. The same five pages with a shared offline theme,
help, filters, comparison tables and diagnostics in labelled sections you can
open, better keyboard and screen-reader support, and a narrow-screen layout. The
engine, rewards and the local API are unchanged.

0.6.4: the panel's loadout notes and the Start question now say the same (they
still said a changed loadout would refuse the claim).

0.6.3: a claim needs only the recorded hero, game version and region. Gear,
talents, levels or combat settings changed after the calibration (for example the
hero levels an expedition's XP brings) no longer block it; the calibrated pace
still sets the rewards.

## Getting started

Extract the entire ZIP to a folder and open **AFK FARM.exe**.
The local panel opens in your browser. No Python installation, pip or account is needed.
Keep `runtime`, `app` and `hs-game-sdk` alongside the EXE; do not move the EXE alone.
All interface text, messages and launcher dialogs are in English.

The world map supports click-and-drag panning, pointer-centered wheel zoom and
keyboard navigation. Region markers can be grabbed without accidentally selecting
them. Use ◎ to return to the selected region.

1. In **Settings**, select the `bin` folder containing `Hero_Siege.exe`.
2. Close the game and click **Install AFK plugin**. Aurie and YYToolkit must already
   be installed; they and the game are not included. Your previous AFK DLL is backed up.
3. Select your offline hero at the top and click **Launch game**. Wait for the hero
   to load in town.
4. Enter a regular Act region. In **Calibration**, click **Start calibration**, play
   your usual route, then click **Finish and save profile**.
5. Save independent MF, XP, Gold and drop settings in **Modifiers**. Defaults are ×1.
   In **Explore**, select the calibrated region and a duration from 15 minutes to
   8 hours, then click **Start expedition**. No game connection is required to start.
   Play another hero or close the game and panel while time continues.
6. Return to the same region with the same hero and loadout. Click **Claim rewards**.
   If Item Editor is offline, items wait locally. Use **Loot → Transfer to Vault**
   when Item Editor is available again.

## What calibration measures

Combat, walking and idle time in the region all count. Time in town is excluded
from the selected region. Use **Restart region** in Calibration to return through town and reopen the region.
Loading pauses the same recording; a changed loadout still invalidates it.
Finish recording before taking a break. At least 1 minute,
30 kills and 95% capture coverage are required; these are minimum thresholds, not
accuracy guarantees. Complete your normal route a few times. You do not need to
wait for a rare item to drop, but its monster/event source must be covered.

Active equipment definitions, the selected equipment set, talents, character level,
difficulty and ForgePact combat settings are fingerprinted. Changes require a new
calibration. Independent reward settings can change between expeditions without
remeasuring pace. Keep settings unchanged during a recording. Game updates do not
automatically validate older profiles. Legacy
profiles are retained, but cannot be used by the panel until recalibrated.

## Current limits

- Offline mode, one local player and one regular Act region per expedition.
- Event completion rewards, guaranteed boss drops, ForgePact kill-trigger rolls and
  Tracker statistic transfers are not fully reproduced.
- Temporary buffs contribute to the measured average; they are not simulated separately.
- Completed native saves can be recovered without replay when plan, checkpoint and spool agree.
  Uncertain partial crashes remain blocked and expose a recovery report.
- Item transfers require Item Editor / Infinite Vault. XP/gold saves and item transfers
  are tracked as separate stages.
- Automatic equipped-character rendering is not available. You can associate a PNG
  screenshot with each character; it remains visible while the game is closed.
- Automatic character setup is tied to the verified game EXE. This package has not
  been validated against every game version.
- A short live calibration-to-claim test passed before 0.5.0 for Suh on the verified build, including
  saved XP/gold after relaunch and Vault deduplication. Independent farming-rate and
  rare-drop accuracy remain unproven; this is not validation for every hero/build.

## Source development

From a source checkout, run `py -3 -B tools/panel.py` with Python 3.13 (stdlib only).
`plugin_build/build.bat` builds the DLL; `launcher/build.bat` builds the launcher.
`py -3 -B tools/build_release.py` creates the portable package using local Python 3.13.
Run `py -3 -B -m unittest discover -s tests -p "test_*.py"` and
`tests/build_and_run.bat` for regression checks.
`tools/game_session.py` (and the panel through it) needs the Python `hs-game-sdk`.
Set `HS_GAME_SDK` to a checkout, or keep one beside this repository as the hub does;
see [test sessions](docs/TEST_SESSIONS.md).
Frontend state regressions use `node --test tests/test_panel_ui.cjs` (Node is a
development test tool only, not a product dependency). The isolated manual browser
fixture is `py -3 -B tests/serve_panel_fixture.py`; it serves port 9567 and writes
its temporary directory and stop sentinel to `verification/independent-rewards-0.5.0/fixture-info.json`
(a local, git-ignored folder).
It only permits timer start/cancel and modifier settings; it never connects to the game or real save data.

Live verification reports (0.4.1 offline start, 0.5.0 independent rewards, the 0.6.x
deliveries) are kept outside this public repository: they contain local paths and the
tester's characters and items.

Source documentation: [UI contract](docs/UI_CONTRACT.md), [architecture](docs/DESIGN.md),
[handover](docs/CHATGPT_HANDOVER.md), [background game setup](docs/TEST_SESSIONS.md).
Runtime data is stored under `%LOCALAPPDATA%/Hero_Siege/afk`.
Previous combat research is archived outside the source tree at
`../../AFK-Research-Archive/2026-09-21`.

## Measurement quality and independent validation

Calibration compares complete one-minute region-clock windows, including zero-kill
windows. The chart shows observed variation, not a confidence interval. Ten minutes,
200 kills, variation at most 25% and first/second-half drift at most 20% are engineering
recommendations for a steady sample, not proof of accuracy. A variable or short
sample is labelled explicitly. The original 3-minute / 30-kill eligibility minimum remains.

Use **Record validation run** to freeze a forecast before recording a separate
session. Finish with the recording button. Validation preserves the original
profile and compares kills and XP against a declared 20% error target; both samples
must reach 10 minutes / 200 kills / 95% usable packet coverage for a passing result.
A short sample, mismatched loadout or same-session comparison cannot report a pass.
Rare-drop probability and live gold parity are not established by this check.

## Recovery, loot and setup

**Recover saved claim** reconciles a completed native save and matching complete
spool without issuing a reward command. The game need not be running. Vault transfer
is a separate retryable step. A failure sidecar, changed plan, unsaved or partial
checkpoint, incomplete spool or inconsistent totals blocks recovery and transfer.
The recovery report explains the missing evidence; no automatic rollback edits saves.
**Close as partial delivery** lets the player accept such a claim as it stands: the
delivered records are kept and can be transferred, the rest is given up, nothing
is replayed, and the expedition clock is freed. When a crash left a running
checkpoint whose item records end exactly at its position (0.6.1 checkpoints record
the spool size, so records written after it are set aside first),
**Continue from recorded position** lets the player deliver the rest instead. It is
never automatic, delivered calls are not repeated, and the save of the delivered
XP and gold stays unconfirmed.
Older receipts that confirm only the controller's room-end save need review: that
path did not reliably persist character XP. The corrected path explicitly saves
both the character and account through native game routines.

Loot shows recent expedition totals, native item cards, search, rarity and game-filter
controls, and local favorites. Up to 500 items per expedition are previewed; the full
spool is preserved and transferred. Native rarity labels are never guessed for unknown
tiers. Existing Item Editor display names and PNG icons are reused with provenance
in `web/assets/items/SOURCE.json`. Settings separately checks the game build, Aurie,
YYToolkit and installed AFK DLL and lists the supported and unsupported mechanics.

The five-step progress was verified live for 0.4.0; that report is kept outside this
public repository.

## Independent reward settings

See [Independent rewards](docs/INDEPENDENT_REWARDS.md) for modifier behavior, native MF
rounding, one-time recalibration and optional ForgePact compatibility.


### Calibration versus an offline expedition

Calibration records the hero currently being played in Hero Siege. Selecting a
hero in AFK FARM selects a saved profile; it does not change the hero in the game.
Use **Explore → Start expedition** with a usable profile to start the timer while
the game is closed or another hero is loaded. The game is needed for the initial
recording and again to generate/claim rewards.

The calibration page links directly to **Open expedition planner**. Game-launch
actions are offered only with the game closed or on a confirmed main/character
selection menu; they do not replace a loaded character.
