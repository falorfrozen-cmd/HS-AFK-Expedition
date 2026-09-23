# UI refresh verification — 2026-09-24

Branch: `chatgpt/ui`. Scope: local web presentation, tests and UI documentation.

## Automated checks

- `node --test tests/test_panel_ui.cjs`: 27 passing tests.
- `node --test tests/test_map_ui.cjs`: 11 passing tests.
- `py -3 -B -m unittest discover -s tests -p "test_*.py"`: 143 passing tests.
- Syntax checks for app.js, extras.js, modifiers.js and qol.js.

New regressions cover disclosure state across rerenders/navigation, detached toggle
events, control focus, page navigation focus, accessible duration preset updates,
partial/saved/transferred presentation, and waiting for all deferred modules before
startup. Existing map pointer, touch, wheel, keyboard and polling tests still pass.

## Browser checks

All checks used `tests/serve_panel_fixture.py` on port 9567 with temporary fake data.
No production panel, game process or real save was used.

- Reviewed all five pages at desktop (1440 × 1000) and narrow (390 × 844) widths.
  No horizontal page overflow; wide data tables scroll inside their containers.
- Map drag moved exactly 100 px horizontally and 50 px vertically without selecting
  another region. Wheel zoom and keyboard Home centering worked.
- Help stayed expanded after changing duration; navigation and modifier stepping
  retained useful keyboard focus. Two keyboard/click increments produced MF ×3,
  then Save modifiers persisted it only in the fixture.
- Started a fake four-hour expedition, selected another hero, confirmed the frozen
  expedition still belonged to Suh, and cancelled through the existing dialog.
- Checked game-closed/no-profile guidance and empty Loot. Checked interrupted claim
  presentation: explicit uncertain-save warning, 400/1,000 calls, one partial-close
  action and 600 abandoned calls stated next to it. No recovery was executed.
- Populated fake loot records: switching expeditions updated both summary and items;
  search produced an honest empty result; saved and transferred labels remained
  separate. Vault filter and filtered-item handling remained available in disclosures.
- A browser reload exposed a pre-existing startup race (`extrasPanel is not defined`).
  Startup now waits for DOMContentLoaded; subsequent reloads rendered without that
  warning and a regression test covers the ordering.

## Limits

Native reward generation, game saving, installation, live calibration, recovery and
Vault transfer were not executed in this UI-only pass. The fixture deliberately
allows only local timer actions and saving modifier defaults. Existing confirmations
and backend action gates remain unchanged. This report does not establish loot
parity or statistical accuracy.

The preview helper is stopped through the stop file specified by its fixture-info.json.
