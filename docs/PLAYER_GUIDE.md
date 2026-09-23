# AFK FARM 0.5.1 beta

Timed offline expeditions using your hero's measured farming pace and the game's
native reward system. Windows only. The interface is entirely in English.

While an expedition's timer runs, the game may be closed or running another hero.
Once you claim, keep the recorded hero in its region with the game open until
delivery finishes. Large claims may take several minutes. Explore shows delivery
progress, and Loot shows provisional generated/filtered item counts. A paused
delivery is distinct from an interrupted claim that needs recovery review.

## Start here

Extract the entire ZIP, then open **AFK FARM.exe**. Keep the `app`, `runtime` and
`hs-game-sdk` folders beside it. You do not need to install Python or pip.

1. Open **Settings** and select the game's `bin` folder containing `Hero_Siege.exe`.
2. Aurie and YYToolkit must already be installed for your game version. Close the
   game and click **Install AFK plugin**. Your previous AFK DLL is backed up.
3. Select your offline hero and click **Launch game**. Automatic startup supports
   the verified executable only; read the setup checklist if it is unavailable.
4. Enter a regular Act region. In **Calibration**, click **Start calibration**,
   farm your usual route, then **Finish and save profile**. Gear, talents, difficulty
   and ForgePact settings must stay the same.
5. When the region is cleared, use **Calibration → Restart region**. This returns
   through town and reloads that region while retaining the same recording.
6. Save your independent reward settings in **Modifiers**. All defaults are ×1.
   Choose your calibrated region and duration in **Explore**, then **Start expedition**.
   Start directly from the saved profile: the game can be closed or running another
   hero. Time continues while the game and panel are closed. Claiming early uses elapsed time.
7. Load the same hero and return to that region with the same loadout. Click
   **Claim rewards**. XP and gold save through the game; native item records go to
   Item Editor's Infinite Vault. If Item Editor is closed, retry **Transfer to Vault**
   from Loot when it is available. Never replay rewards to repair an item transfer.

## Exploring the map

Hold the left mouse button and drag anywhere on the map, including a region marker.
A drag moves the map; a click selects a region. Scroll to zoom toward the pointer,
or use the − / + controls. The percentage shows the current zoom; ◎ returns to the
selected region. The camera stops at the map's edges.

On touch screens, drag with one finger and use the zoom buttons. Keyboard users can
focus **Map navigation** and use arrow keys to pan (Shift for larger steps), + / −
to zoom, and Home to center the selection. Region buttons also support normal
keyboard activation. Map navigation does not require the game to be running.

## Reading your calibration

The result card distinguishes **Calibration saved** from **No profile saved**.
Time is displayed as minutes:seconds in the recorded region. While recording, the
card shows time and kill progress; **Finish and save profile** enables after both
minimums are met. Finishing then checks usable reward records before reporting
success. **Stop without saving a profile** ends a short recording and preserves
its raw data; it does not produce a usable expedition profile.

Keep the same recording running across **Restart region** when a route is short.
Stopped recordings do not automatically merge into a new recording. After a
successful save, **Use saved calibration** selects that exact hero and region in
Explore. Each usable profile also has a **Use profile** button. If no usable profile
was saved, Explore shows the recording's reason instead of a generic missing-profile
message. An older usable profile remains available if a newer recording fails.

The selected hero and the hero currently loaded in the game are separate. The
calibration page labels the live hero/location and the last recording individually.
To record, load the selected hero in a regular Act region. To start an expedition
from an existing profile, no live hero is required. You can change the selected hero
or record another hero while an expedition runs; the active plan stays unchanged.
Return to the recorded hero, loadout and region when claiming rewards.

Walking and idle time in the region count. Town and loading time do not count for
that region. Finish before taking a break. One minute, 30 kills and 95% capture
coverage are minimum eligibility checks, not accuracy guarantees. Additional normal routes are optional and provide more evidence. The chart describes one-minute pace
variation; it is not a confidence interval.

**Record validation run** freezes a profile's forecast, then starts a separate
measurement without replacing the profile. Both recordings must reach 10 minutes,
200 kills and 95% coverage to pass the declared 20% kill/XP error target. A single
comparison does not establish accuracy for other characters or rare items.

## Your loot and records

Loot shows the latest 20 expeditions and up to 500 item previews per expedition.
The complete item records remain on disk. Game-filtered items are hidden and are
excluded from Vault transfer by default. Use **Show filtered items** to inspect
them. Search, rarity controls and favorites help you browse. Unknown rarity codes
remain numbered instead of receiving a guessed label.

You can choose a PNG character screenshot in Settings. It stays on this computer;
it is not an automatic rendering of your current equipment.

## Recovery and limits

**Recover saved claim** is available only when a complete native save, plan and
item records agree. It reconciles bookkeeping without generating rewards again.
Partial or contradictory delivery remains blocked; open the recovery report.
Older room-end-only save receipts require review. Game saves and item records are
not one atomic transaction, so recovery is not guaranteed after power loss.

- One offline local hero and one regular Act region per expedition.
- Temporary buffs affect the measured average; future combat is not simulated.
- Event completion rewards, guaranteed boss rewards, ForgePact kill-trigger rolls
  and Tracker kill counters are not reproduced completely.
- Keys, relic gates and rare-drop parity still need independent verification.
- Changed gear, talents, difficulty, combat settings or game builds require recalibration.
  AFK reward settings can change between expeditions without measuring pace again.
- A short end-to-end test passed for the earlier reward policy on one hero/build. General accuracy is unproven.

Runtime data and plugin backups are under `%LOCALAPPDATA%\Hero_Siege\afk`.
The ZIP contains no game files, saves, Aurie or YYToolkit. Keep the complete package
together and use the included setup checklist before your first expedition.

## Independent reward settings

See [Independent rewards](INDEPENDENT_REWARDS.md) for modifier behavior, native MF
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
