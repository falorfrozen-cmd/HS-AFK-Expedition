# Independent rewards — 0.5.0

AFK FARM owns its reward settings. ForgePact is optional. Calibration supplies
kills per region-second and the observed mix of monsters; combat speed and monster
density therefore remain part of that measurement. This is not a combat simulator.

## Player contract

The English **Modifiers** page saves Magic Find, XP, Gold and 14 drop-rate families:
Dungeon Keys, Angelic Keys, Chaos + Crystal Keys, Bifrost Key, Relics, Runes,
Gems, Boss Gems, Orbs, Scrolls of Ra, Dimensional Shards, Battle Fragments,
Colosseum Fragments and Ruby Keys. Mining Ore and extra Angelic/Unholy item rolls
are excluded. Values are finite numbers from ×1 through ×100; ×1 is native behavior.

Save settings before starting. The expedition stores a copy, and later edits apply
to the next expedition. The active card shows its recorded settings. Changing
these settings does not change calibrated kills/minute. Reward-only ForgePact
settings are also excluded from the claim-time pace comparison; combat settings,
gear, talents, identity, difficulty and build remain checked. Keep all game settings
unchanged during the recording itself.

Magic Find scales the same native stat component used by ForgePact. The game keeps
its normal final processing and rounding. The offline display is an estimate from
recorded MF, not a promise that the rounded displayed base multiplied by 40 equals
the game's final value. On the tested hero, native MF was 1320 and the native ×40
result was 52838. Actual MF is read before delivery and stored in the progress receipt.
It does not mean a guaranteed rarity or 40 times as many rare items.

XP uses the native spawn-time EnemyCalculateExperience result observed for each
source, without ForgePact reward multipliers, averaged over its recorded kills.
An independent plan applies AFK XP once. The compatibility plugin neutralizes both
the direct XP multiplier and the percentage XP stat during this read. It does not
award XP or alter active combat. Old captures lack this evidence and need a new
normal-route calibration; changing a legacy profile's version cannot repair it.

Gold repeats native gold-drop calls, with a fractional extra attempt for decimal
multipliers. It scales the expected amount rather than promising identical coin
piles. The guard prevents nested gold routines from applying it twice.

Other rates lower the native repository denominator with a minimum denominator
of 1. Native drop gates still apply. Enabled Dungeon/Angelic Key modifiers add a
missing gate; Relics additionally use the established capped extra-attempt curve.
All actual items are created by the game. This does not add event completion
rewards, guaranteed boss loot, perfect-gem pools or arbitrary item types.

## Native boundary and optional ForgePact

`hs_game_sdk/reward_scope.hpp` is a process-local, game-thread-owned cooperative
scope. Both plugins use the same named mapping; neither calls the other's symbols.
The updated ForgePact bypasses its reward multipliers only inside this scope and
publishes original repository denominators. AFK restores changed denominators
after each replay batch, including exception unwinding. Combat modifiers and the
ForgePact configuration file are not changed.

An older ForgePact DLL without this protocol cannot safely coexist with independent
rewards. Calibration/claim refuses it with an update message. It is never treated
as vanilla merely because a config file is absent. ForgePact is detected through
the loader as well as normal Windows module lookup. The existing installed
ForgePact is not silently replaced with a different feature branch.

The compatibility source changes are present in the sibling ForgePact directory
and the current `forgepact-ore-20260921` ForgePact worktree. They must be built
into the ForgePact version being distributed. The AFK ZIP does not install or require
ForgePact. With no ForgePact loaded, AFK's own plugin handles the whole policy.

## Data and verification

- `reward-modifiers.json`: saved defaults for the next expedition, schema 1.
- `session_start.reward_baseline`: native MF and availability evidence.
- `kill.native_kill_exp`: observed native XP, or null when unavailable.
- `profile.reward_baseline.complete`: requires evidence for all used kill packets.
- `plan.reward_modifiers`, `reward_baseline`, packet `native_exp`: frozen policy.
- `progress.effective_magic_find`: native value confirmed at delivery.

Old learned item/gold forecasts are hidden for independent plans because they
may have been collected under another reward policy. Existing legacy plans keep
their original reward rules; they are not retroactively changed.

`afk rewards probe` checks native MF scaling, all 14 repository families, restoration
and available monster XP evidence without spawning loot, awarding XP or saving a
character. It writes `verification/reward-probe.json` under the AFK data directory.
This is a mechanism check, not statistical validation of rare-drop distributions.
Two additional native replay smoke tests completed 360 calls with independent
modifiers, no failed/skipped calls, and no player XP/gold or Vault credits.
Diagnostic item records are stored with the verification evidence, outside live spool.
The original end-to-end save/Vault tests predate this policy and must not be presented
as end-to-end verification of every new modifier.

Session reads now request an isolated response filename, then remove it after
reading. A second panel cannot overwrite another request's JSON before it is read.
The old common response remains supported for older clients.
