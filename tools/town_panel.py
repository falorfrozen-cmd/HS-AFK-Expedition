"""The town in the panel (0.9): fortifications, sieges, merchants, wagons and the coffer.

A mixin of ``panel.Panel``. Everything the town does is local and instant, with
three exceptions that go through the game with one receipt per request:
- gold into the town's coffer from the loaded hero (the purchase path, like
  every camp payment);
- gold out of the coffer to the loaded hero (``afk worker credit``);
- goods out of the camp's stock into the Vault, made by the game
  (``afk worker deliver`` with a ``worker_town_`` plan).
A siege's rewards go through the game too, as replays in its region:
- a stationed hero's share is that hero's claim;
- the town's share is collected like a worker's haul.

Fortifications, merchants and wagons pay from the coffer and earn into it, so
the town runs while the game is closed.

Pages never write. A page sees the town settled in memory: finished builds,
wagons that reached their towns, a siege that ended. Actions (in the job
thread) and the monitor (only between actions) persist the settlement. So a
page can never write over an action's change.

State:
- workers.json: ``town``, ``trade``, ``market`` and ``credits``, top-level
  sections next to the camp;
- each siege's record: ``plans/defense_<id>.json``.

Standard library only.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import afk
import battle
import bestiary
import camp
import defense
import fortifications as F
import goods
import merchants
import recovery
import reward_modifiers
import town
import trade
import vault_take
import workers
import worker_loot

MAX_COFFER_MOVE = 500_000_000          # the game's gold cap
MAX_STONE_BUDGET = 1_000_000
# The watch keeps the town under siege, towers only, one siege after another. A siege it
# starts while the panel is closed begins where the last one ended (at most a day back),
# so it catches up when the panel opens again, a few sieges at a time; it pauses while
# WATCH_WAITING town shares wait to be collected, so no loot piles up out of reach.
WATCH_CATCH_UP = 4
WATCH_LOOKBACK_HOURS = 24
WATCH_WAITING = 8
TOWN_LOCAL = ('fort_build', 'fort_upgrade', 'fort_plating', 'fort_arrange', 'wall_repair', 'defense_start', 'defense_repair',
              'defense_retreat', 'defense_watch', 'trade_send', 'trade_unload', 'market_buy', 'market_sell')
TOWN_GAME = ('coffer_deposit', 'coffer_collect', 'defense_collect', 'stock_send', 'stock_close_partial')
TOWN_ACTIONS = TOWN_LOCAL + TOWN_GAME


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _whole(value, low, high, what) -> int:
    _require(type(value) is int and low <= value <= high, f'{what} must be a whole number from {low:,} to {high:,}.')
    return value


class TownPanel:
    # ------------------------------------------------------------------ state
    def town_load(self, at=None, persist=True) -> dict:
        """workers.json with the town's sections ready and everything due settled.

        ``persist``: write what settled; only actions and the monitor (between actions)
        load this way. A page loads with ``persist=False`` and can never save the result."""
        at = at or datetime.now(timezone.utc).replace(microsecond=0)
        state = workers.load(self.data, at)
        state['town'] = town.normalize(state.get('town'))
        trade.ensure(state)
        state['market'] = merchants.normalize(state.get('market'))
        state['credits'] = state['credits'] if isinstance(state.get('credits'), dict) else {}
        changed = bool(town.settle(state['town'], state['camp'], at))
        changed |= bool(trade.settle(state['trade'], at))
        changed |= merchants.tidy(state['market'], state['camp'], at)
        changed |= self.defense_settle(state, at, persist)
        if persist:
            changed |= self.defense_watch_step(state, at)
        state['_writable'] = persist
        if persist and changed:
            self.town_save(state)
        return state

    def settle_town_late(self):
        """From the monitor: persist what settled by the clock, only between actions."""
        try:
            if not self.job_lock.acquire(blocking=False):
                return
            try:
                self.town_load(persist=True)
            finally:
                self.job_lock.release()
        except Exception as error:
            if str(error) != getattr(self, 'town_error', None):
                self.town_error = str(error)
                self.note(f'The town could not be brought up to date: {error}')

    def town_save(self, state) -> None:
        if not state.pop('_writable', False):
            raise RuntimeError('a page tried to write the town; only actions and the monitor may')
        try:
            workers.save(self.data, state)
        finally:
            state['_writable'] = True

    # ------------------------------------------------------------------ views
    def town_view(self) -> dict:
        at = datetime.now(timezone.utc).replace(microsecond=0)
        state = self.town_load(at, persist=False)
        out = town.view(state['town'], state['camp'], at)
        out['coffer'] = state['trade']['coffer']
        out['stock'] = [dict(key=k, name=goods.name(k), category=goods.category(k), count=n, value=goods.value(k) if goods.known(k) else None)
                        for k, n in sorted(state['camp']['stock'].items(), key=lambda kv: (goods.category(kv[0]) or '', goods.name(kv[0])))]
        out['key_rack'] = dict(state['camp']['keys'])
        out['stone'] = state['camp']['resources'].get('stone', 0)
        out['pending_credits'] = [dict(request_id=k, **{x: v.get(x) for x in ('amount', 'state', 'error')})
                                  for k, v in state['credits'].items() if v.get('state') in ('pending', 'unknown', 'refused', 'review')][-5:]
        out['goods'] = goods.catalog()
        out['category_names'] = goods.CATEGORY_NAMES
        return out

    def defense_view(self) -> dict:
        at = datetime.now(timezone.utc).replace(microsecond=0)
        state = self.town_load(at, persist=False)
        siege = state['town'].get('siege')
        record = self.defense_record(siege) if siege else None
        watch = camp.effects(state['camp'])['scout']
        pool = worker_loot.pools()
        regions = []
        names = self.zone_names_map()
        for r in bestiary.regions():
            if not str(r['room']).startswith('Act_'):
                continue
            goblins = sorted(k for k, v in (pool.get('goblins', {}).get(r['room']) or {}).items() if v)
            regions.append(dict(r, name=names.get(r['room'], r['room']), goblins=goblins,
                                calibrated=[dict(slot=p['character'].get('slot'), name=p['character'].get('name'), profile=p['id'])
                                            for p in self.profiles if p.get('usable') and p.get('room') == r['room']]))
        return dict(siege=defense.view(record, at, watch) if record else None, watch=state['town'].get('watch'),
                    waiting=[dict((k, h[k]) for k in ('id', 'room', 'region', 'level', 'outcome', 'kills', 'ended_at'))
                             for h in state['town']['history'] if not h.get('town_collected')],
                    history=state['town']['history'][:10],
                    records=defense.load_records(self.data)['regions'], regions=regions, limits=town.limits(state['camp']),
                    levels=dict(max=defense.MAX_LEVEL, min_hours=defense.MIN_HOURS, max_hours=defense.MAX_HOURS, wave_minutes=defense.WAVE_MINUTES),
                    rank_names=bestiary.RANK_NAMES, tiers=[dict(t) for t in defense.TIERS], events=[dict(e) for e in defense.EVENTS],
                    affixes={str(k): dict(v) for k, v in defense.AFFIXES.items()}, towers=[dict(t) for t in F.TOWERS])

    def bestiary_view(self, room: str) -> dict:
        state = self.town_load(persist=False)
        entries = bestiary.region(room)
        _require(entries, 'The bestiary knows no monster of that region yet.')
        slain = state['town']['slain']
        out = []
        for key, e in sorted(entries.items(), key=lambda kv: (kv[1]['rank'], kv[1]['name'])):
            out.append(dict(key=key, name=e['name'], names=e['names'], rank=e['rank'], rank_name=e['rank_name'], origin=e['origin'],
                            speed=e['speed'], ranged=e['ranged'], immune=e['immune'], flyer=defense.is_flyer(e),
                            affixes=[dict(id=a, name=defense.affix(a)['name'], seen=n) for a, n in e['affixes'].items()],
                            recorded=e['recorded'], slain=sum(n for k, n in slain.items() if k == key)))
        return dict(room=room, name=self.zone_names_map().get(room, room), entries=out)

    def trade_view(self) -> dict:
        at = datetime.now(timezone.utc).replace(microsecond=0)
        state = self.town_load(at, persist=False)
        return trade.view(state['trade'], state['camp'], at)

    def market_view(self) -> dict:
        at = datetime.now(timezone.utc).replace(microsecond=0)
        state = self.town_load(at, persist=False)
        out = merchants.view(state['market'], state['camp'], at)
        out['coffer'] = state['trade']['coffer']
        return out

    def defense_forecast(self, args: dict) -> dict:
        """What a siege at a level usually looks like for the town as it stands now (simulated)."""
        state = self.town_load(persist=False)
        trial = self.defense_trial(state, args)
        level = args.get('level')
        if level is None:
            return dict(suggested=defense.suggest_level(trial))
        _whole(level, 1, defense.MAX_LEVEL, 'The level')
        return defense.forecast(trial, level, min(trial['waves_total'], 24))

    def zone_names_map(self) -> dict:
        import panel
        return panel.zone_names()

    def town_summary(self) -> dict:
        """The town in /api/state: the coffer, the siege, wagons home, merchants in town."""
        try:
            at = datetime.now(timezone.utc).replace(microsecond=0)
            state = self.town_load(at, persist=False)
            siege = state['town'].get('siege')
            view = None
            if siege and not siege.get('settled'):
                record = self.defense_record(siege)
                v = defense.view(record, at)
                view = {k: v[k] for k in ('id', 'region', 'level', 'waves_done', 'waves_total', 'wave', 'next_wave_at', 'over', 'outcome', 'walls', 'keep')}
            return dict(coffer=state['trade']['coffer'], siege=view,
                        town_shares=sum(1 for h in state['town']['history'] if not h.get('town_collected')),
                        wagons_home=len(trade.returned(state['trade'], at)), merchants=len(merchants.visits(state['camp'], at)),
                        building=len(state['town']['queue']))
        except (OSError, ValueError, KeyError) as error:
            return dict(error=str(error))

    def defense_row(self, plan: dict) -> dict | None:
        """A stationed hero's roster row: its siege as it stands, and whether its claim is ready."""
        try:
            import panel
            record = panel.read(Path(plan['defense']['record']), None)
            if not isinstance(record, dict) or record.get('id') != plan['defense']['id']:
                return None
            v = defense.view(record)
            return dict(ready=v['over'], ready_at=v['ends_at'],
                        defense={k: v[k] for k in ('id', 'region', 'level', 'waves_done', 'waves_total', 'wave', 'over', 'outcome')})
        except (OSError, ValueError, KeyError):
            return None

    # ------------------------------------------------------------------ actions
    def town_action(self, name: str, args: dict):
        at = datetime.now(timezone.utc).replace(microsecond=0)
        self.town_load(at, persist=True)
        if name == 'coffer_deposit':
            return self.coffer_deposit(_whole(args.get('amount'), 1, MAX_COFFER_MOVE, 'The amount'))
        if name == 'coffer_collect':
            return self.coffer_collect(_whole(args.get('amount'), 1, MAX_COFFER_MOVE, 'The amount'))
        if name == 'defense_collect':
            return self.defense_collect(args.get('siege'), every=args.get('all') is True)
        if name == 'stock_send':
            return self.stock_send(args.get('items'))
        if name == 'stock_close_partial':
            return self.stock_close_partial()
        state = self.town_load(at)
        t, c, tr = state['town'], state['camp'], state['trade']
        if name in ('fort_build', 'fort_upgrade', 'fort_plating'):
            if name == 'fort_build':
                plan = town.next_tower(t, c, args.get('kind'), args.get('place'))
            elif name == 'fort_upgrade':
                plan = town.next_upgrade(t, c, str(args.get('tower', '')))
            else:
                plan = town.next_plating(t, c, args.get('side'))
            gold = (plan.get('cost') or {}).get('gold', 0)
            if gold > tr['coffer']:
                plan['blockers'].append(f"the coffer holds {tr['coffer']:,} of {gold:,} gold")
            _require(plan.get('to') and not plan['blockers'], 'Cannot build: ' + '; '.join(plan['blockers'] or ['nothing to build']) + '.')
            camp.take(c, {k: v for k, v in plan['cost'].items() if k in camp.RESOURCES})
            town.take_materials(c, plan['materials'])
            tr['coffer'] -= gold
            entry = town.start(t, plan, request=uuid.uuid4().hex, at=at)
            self.town_save(state)
            what = F.TOWER_BY_KEY[plan['kind']]['name'] if plan.get('kind') else f"{plan['side'].title()} wall plating"
            self.log(f"{what} level {plan['to']} is being built ({gold:,} gold from the coffer); ready at {entry['ready_at']}.")
            return entry
        if name == 'fort_arrange':
            tower = town.arrange(t, str(args.get('tower', '')), place=args.get('place'), priority=args.get('priority'), perk=args.get('perk'))
            self.town_save(state)
            self.log(f"{F.TOWER_BY_KEY[tower['kind']]['name']} {tower['id']}: at the {tower['place']}, "
                     f"aiming at {tower.get('priority') or F.TOWER_BY_KEY[tower['kind']]['priority']}"
                     + (f", specialised as {tower['perk']}" if tower.get('perk') else '') + '.')
            return tower
        if name == 'wall_repair':
            done = town.repair_now(t, c, _whole(args.get('stone'), 1, MAX_STONE_BUDGET, 'The stone'), at)
            self.town_save(state)
            self.log(f"The masons used {done['stone']:,} stone: " + ', '.join(f'{s} +{hp:,.0f}' for s, hp in done['walls'].items()) + '.')
            return done
        if name == 'defense_start':
            return self.defense_start(state, args, at)
        if name == 'defense_watch':
            return self.defense_watch(state, args, at)
        if name in ('defense_repair', 'defense_retreat'):
            siege = t.get('siege')
            _require(siege and not siege.get('settled'), 'The town is not under siege.')
            with recovery.data_lock(self.data):
                record = self.defense_record(siege)
                if name == 'defense_repair':
                    stone = _whole(args.get('stone'), 1, MAX_STONE_BUDGET, 'The stone')
                    _require(c['resources'].get('stone', 0) >= stone, f"The camp has only {c['resources'].get('stone', 0):,} stone.")
                    done = defense.repair(record, stone, at)
                    c['resources']['stone'] -= done['stone']
                    self.log(f"Between waves the masons used {done['stone']:,} stone on the walls.")
                else:
                    done = defense.retreat(record, at)
                    self.log(f"The retreat was sounded after wave {done}. The heroes and the town can collect what they earned.")
                afk.write_json(Path(siege['path']), record)
            self.town_save(state)
            return done
        if name == 'trade_send':
            plan = trade.plan_run(tr, c, args.get('town'), args.get('cargo'), args.get('orders'), args.get('purse', 0), at)
            _require(plan['purse'] <= tr['coffer'], f"The coffer holds only {tr['coffer']:,} gold.")
            tr['coffer'] -= plan['purse']
            run = trade.start_run(tr, c, plan, 'wagon_' + uuid.uuid4().hex[:10], plan['purse'], at=at)
            self.town_save(state)
            self.log(f"A wagon left for {trade.TOWN_BY_KEY[run['town']]['name']}; it arrives at {run['arrives_at']} and is home at {run['returns_at']}.")
            return run
        if name == 'trade_unload':
            done = trade.unload(tr, c, str(args.get('run', '')), at)
            self.town_save(state)
            e = done['entry']
            self.log(f"The wagon from {trade.TOWN_BY_KEY[e['town']]['name']} is home: earned {e['earned']:,}, spent {e['spent']:,}, "
                     f"{e['gold_back']:,} gold into the coffer" + (f"; brought {goods.describe(e['bought'])}" if e['bought'] else '') + '.')
            return done
        if name in ('market_buy', 'market_sell'):
            visit = merchants.find(c, str(args.get('visit', '')), at)
            key = str(args.get('good', ''))
            qty = args.get('count')
            q = merchants.quote(state['market'], visit, key, 'sell' if name == 'market_buy' else 'buy', qty)
            if name == 'market_buy':
                _require(q['gold'] <= tr['coffer'], f"That costs {q['gold']:,} gold; the coffer holds {tr['coffer']:,}.")
                room = camp.effects(c)['stock_cap'] - sum(c['stock'].values())
                _require(key in trade.RACK_KEYS or q['qty'] <= room, f'The stock has room for {max(0, room):,} more.')
                tr['coffer'] -= q['gold']
                trade.arrive(c, {key: q['qty']})
            else:
                _require(c['stock'].get(key, 0) >= q['qty'] or (key in trade.RACK_KEYS and c['keys'].get(key.split(':')[1], 0) >= q['qty']),
                         f'The camp has only {self._held(c, key):,} {goods.name(key)}.')
                self._take_goods(c, {key: q['qty']})
                tr['coffer'] += q['gold']
            entry = merchants.record(state['market'], visit, q, at)
            self.town_save(state)
            self.log(f"{'Bought' if name == 'market_buy' else 'Sold'} {q['qty']:,} {q['name']} "
                     f"{'from' if name == 'market_buy' else 'to'} {visit['name']} for {q['gold']:,} gold.")
            return entry
        raise ValueError('Unknown town action.')

    @staticmethod
    def _held(c, key) -> int:
        if key in trade.RACK_KEYS:
            return c['keys'].get(key.split(':')[1], 0)
        return c['stock'].get(key, 0)

    @staticmethod
    def _take_goods(c, items: dict) -> None:
        for key, n in items.items():
            if key in trade.RACK_KEYS:
                base = key.split(':')[1]
                c['keys'][base] -= n
                if not c['keys'][base]:
                    del c['keys'][base]
            else:
                c['stock'][key] -= n
                if not c['stock'][key]:
                    del c['stock'][key]

    # ------------------------------------------------------------------ the coffer and the game's gold
    def coffer_deposit(self, amount: int):
        """Gold from the loaded hero into the town's coffer, through the game's purchase path."""
        paid = self.worker_pay('deposit', amount)
        if paid is None:
            return None
        self.log(f'{amount:,} gold went into the town\'s coffer.')
        return paid

    def credit_receipt(self, request):
        import panel
        return panel.read(self.data / 'models' / f'worker-credit-{request}.json', {}) or {}

    def send_credit(self, request, amount):
        reply = afk.Ipc(self.game_bin()).send(f'afk worker credit {request} {int(amount)}', timeout=60)
        receipt = self.credit_receipt(request)
        return receipt if receipt or reply is not None else None

    def record_credit(self, request, receipt):
        """Store what the game did with a credit; the coffer pays it out exactly once."""
        state = self.town_load()
        entry = state['credits'][request]
        if receipt is None:
            entry.update(state='unknown', error='the game did not answer in time')
        elif not receipt:
            # The game answered but left no receipt: a payout may have been made whose receipt
            # could not be written. Never put it back in the coffer blindly.
            entry.update(state='review', error='the game answered without a receipt; check the hero\'s gold')
        else:
            entry.update(state='paid' if receipt.get('ok') is True else 'refused',
                         error=receipt.get('error') or (None if receipt else 'no receipt from the game'), receipt=receipt or None)
            before, after = receipt.get('gold_before'), receipt.get('gold_after')
            if (entry['state'] == 'refused' and isinstance(before, (int, float)) and isinstance(after, (int, float))
                    and before >= 0 and after - before > 0.5):
                # Refused, yet the hero's gold rose (a take-back that failed): the hero may hold the
                # gold. It stays out of the coffer until the player has looked; never paid twice.
                entry.update(state='review', error=(entry.get('error') or '') + f'; the gold rose by {after - before:,.0f} anyway')
            if entry['state'] == 'refused' and not entry.get('released'):
                state['trade']['coffer'] += int(entry['amount'])     # set aside when asked; back into the coffer
                entry['released'] = True
            if entry['state'] == 'paid' and not entry.get('applied'):
                entry['applied'] = True
                self.log(f"{entry['amount']:,} gold left the coffer for {(receipt.get('character') or {}).get('name', 'the hero')} "
                         f"({receipt.get('gold_before'):,.0f} -> {receipt.get('gold_after'):,.0f}); the game saved.")
        self.town_save(state)
        return entry

    def unanswered_credits(self):
        return [k for k, v in self.town_load(persist=False)['credits'].items() if v.get('state') in ('pending', 'unknown')]

    def settle_credits(self):
        for request in self.unanswered_credits():
            receipt = self.credit_receipt(request)
            if receipt:
                self.record_credit(request, receipt)

    def settle_late_credits(self):
        """From the monitor: only between actions."""
        try:
            if not self.unanswered_credits() or not self.job_lock.acquire(blocking=False):
                return
            try:
                self.settle_credits()
            finally:
                self.job_lock.release()
        except Exception as error:
            if str(error) != getattr(self, 'credit_error', None):
                self.credit_error = str(error)
                self.note(f'An earlier coffer payout is not settled yet: {error}')

    def coffer_collect(self, amount: int):
        """Gold from the coffer to the loaded hero, once per request (``afk worker credit``)."""
        s = self.fresh()
        _require(not s['replay_running'], 'Wait for reward delivery to finish.')
        self.settle_credits()
        for request in self.unanswered_credits():
            entry = self.town_load()['credits'][request]
            self.log(f"Finishing an earlier payout of {entry['amount']:,} gold first...")
            entry = self.record_credit(request, self.send_credit(request, entry['amount']))
            _require(entry['state'] != 'unknown', 'The game has not answered an earlier payout yet. Nothing new was paid out; try again with an '
                     'offline hero loaded.')
        state = self.town_load()
        _require(state['trade']['coffer'] >= amount, f"The coffer holds only {state['trade']['coffer']:,} gold.")
        request = uuid.uuid4().hex
        state['trade']['coffer'] -= amount
        state['credits'][request] = dict(amount=amount, state='pending', at=afk.iso(afk.now_utc()), character=s['character'])
        self.town_save(state)
        self.log(f"Paying {amount:,} gold from the coffer to {s['character']['name']} in the game...")
        entry = self.record_credit(request, self.send_credit(request, amount))
        _require(entry['state'] != 'unknown', 'The game did not answer in time. If it pays the gold, AFK FARM records it by itself; '
                 'it is never paid twice.')
        _require(entry['state'] != 'review', "The game refused the payout, yet the hero's gold rose: " + (entry.get('error') or '')
                 + ". The amount is kept out of the coffer until you have checked the hero's gold; it is never paid twice.")
        _require(entry['state'] == 'paid', 'The game did not pay the gold: ' + (entry.get('error') or 'no receipt came back')
                 + '. It stays in the coffer.')
        return entry

    # ------------------------------------------------------------------ goods to the Vault
    def stock_send(self, raw):
        """Goods from the camp's stock to the Vault: the game makes each stack with its
        ground-drop routine (a ``worker_town_`` delivery), then the Vault takes them in."""
        _require(isinstance(raw, dict) and raw, 'Choose the goods to send to the Vault.')
        state = self.town_load()
        items = {}
        for key, n in raw.items():
            _require(goods.known(key), f'{goods.name(key)} is not a town good.')
            _whole(n, 1, 100_000, 'A count')
            _require(self._held(state['camp'], key) >= n, f"The camp has only {self._held(state['camp'], key):,} {goods.name(key)}.")
            items[str(key)] = n
        if state['town'].get('sending'):
            self.log('An earlier shipment to the Vault is not finished; finishing it first (the goods asked now were not taken).')
            return self.finish_stock_send()
        s = self.fresh()
        _require(not s['replay_running'], 'Wait for reward delivery to finish.')
        ident = 'worker_town_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
        plan = dict(schema=1, delivery_id=ident, planned_at=afk.iso(afk.now_utc()), character=s['character'],
                    items=[dict(type=goods.parse(k)[0], id=goods.parse(k)[1], amount=n) for k, n in sorted(items.items())],
                    prospect={}, crafts=[], route_prospect=False, label='AFK · Town · ' + datetime.now().strftime('%Y-%m-%d'))
        path = self.data / 'plans' / f'{ident}.json'
        afk.write_json(path, plan)
        self._take_goods(state['camp'], items)
        state['town']['sending'] = dict(delivery_id=ident, plan=str(path), items=items, at=plan['planned_at'])
        self.town_save(state)
        return self.finish_stock_send()

    def finish_stock_send(self):
        state = self.town_load()
        sending = state['town'].get('sending')
        _require(sending, 'Nothing is being sent to the Vault.')
        ident, path = sending['delivery_id'], Path(sending['plan'])
        result_path = self.data / 'sessions' / f'{ident}.result.json'
        import panel
        spool = self.data / 'spool' / f'{ident}.ndjson'
        if not result_path.exists() and not spool.exists():
            s = self.fresh()
            _require(not s['replay_running'], 'Wait for reward delivery to finish.')
            self.log('The game is making the goods...')
            reply = afk.Ipc(self.game_bin()).send(f'afk worker deliver {path}', timeout=120)
            _require(reply is not None, 'The game did not answer in time. The shipment stays planned: send again to finish it; '
                     'nothing is made twice and nothing is lost.')
            for line in reply:
                self.log(line)
            if not result_path.exists() and not spool.exists():
                # the game answered and made nothing (it refused the plan): the goods go back into the stock
                trade.arrive(state['camp'], sending['items'])
                state['town']['sending'] = None
                self.town_save(state)
                raise ValueError('The game made nothing: ' + ('; '.join(reply) or 'no reason given') + '. The goods are back in the stock.')
        result = panel.read(result_path, {}) or {}
        _require(result.get('state') == 'done', 'The shipment stopped part way ('
                 + (result.get('error') or 'no result came back') + '). Close it as partial: what was made goes to the Vault, '
                 'the rest back into the stock; nothing is made twice.')
        state['town']['sending'] = None
        self.town_save(state)
        self.log(f"The game made {goods.describe(sending['items'])}; sending them to the Vault...")
        self.transfer_worker_haul(ident, 'Town')
        return result

    def stock_close_partial(self):
        """Keep what a stopped shipment made (to the Vault) and put the rest back into the stock.
        Only once the game has worked on it (a result or a spool exists): an unanswered
        shipment might still be made, so it is never given back."""
        import panel
        state = self.town_load()
        sending = state['town'].get('sending')
        _require(sending, 'No shipment is waiting.')
        ident = sending['delivery_id']
        result_path, spool = self.data / 'sessions' / f'{ident}.result.json', self.data / 'spool' / f'{ident}.ndjson'
        result = panel.read(result_path, {}) or {}
        _require(result.get('state') != 'done', 'This shipment finished; send again to take it to the Vault.')
        _require(result or spool.exists(), 'The game has not worked on this shipment yet: send again to finish it.')
        made = {}
        for row in (afk.read_ndjson(spool) if spool.exists() else []):
            item = row.get('item') or {}
            if row.get('kind') != 'item' or not isinstance(item, dict):
                continue
            d = item.get('itemDefinitionStruct') or {}
            key = f"{int(item.get('itemType', -1))}:{int(d.get('b', -1))}"
            made[key] = made.get(key, 0) + int(d.get('o', 1) or 1)
        back = {k: n - made.get(k, 0) for k, n in sending['items'].items() if n > made.get(k, 0)}
        trade.arrive(state['camp'], back)
        afk.write_json(result_path, dict(result, delivery_id=ident, state='partial', partial=True, created=made, settled_by='player',
                                         settled_at=afk.iso(afk.now_utc())))
        state['town']['sending'] = None
        self.town_save(state)
        self.log(f"The shipment was closed: {goods.describe(made) or 'nothing'} made"
                 + (f"; {goods.describe(back)} back in the stock" if back else '') + '.')
        if made:
            self.transfer_worker_haul(ident, 'Town')
        return dict(made=made, back=back)

    # ------------------------------------------------------------------ sieges
    def defense_record(self, siege) -> dict:
        import panel
        record = panel.read(Path(siege['path']), None)
        _require(isinstance(record, dict) and record.get('id') == siege['id'], 'The siege record is missing.')
        return record

    def defense_trial(self, state, args) -> dict:
        """A record-like siege of the town as it stands, for forecasts (no heroes' claims armed)."""
        room = args.get('room')
        entries = bestiary.region(room)
        _require(entries, 'The bestiary knows no monster of that region yet: record a calibration there first.')
        hours = float(args.get('hours') or 2.0)
        snap = defense.snapshot(state['town'], state['camp']['buildings'])
        heroes = self.defense_heroes(state, room, args.get('heroes') or [], arm=False)
        record = defense.new_record('defense_trial', room, 1, hours, town_snapshot=snap,
                                    walls_now={s: snap['walls'][s]['max'] for s in battle.SIDES}, keep_now=snap['keep']['max'],
                                    heroes=heroes, entries=entries, goblins=(worker_loot.pools().get('goblins', {}).get(room) or {}),
                                    stone_budget=int(args.get('stone') or 0), build=bestiary.current_build(), seed=1)
        return record

    def defense_heroes(self, state, room, picks, arm=True) -> list[dict]:
        """The stationed heroes: each needs a usable calibration in the region and must be free."""
        _require(isinstance(picks, list), 'Choose the heroes to station.')
        limit = town.limits(state['camp'])['hero_posts']
        _require(len(picks) <= limit, f'Headquarters level {town.limits(state["camp"])["hq"]} has {limit} hero post'
                 f'{"s" if limit != 1 else ""}.')
        chosen, seen = [], set()
        st = afk.load_state(self.data / 'state.json')
        for pick in picks:
            _require(isinstance(pick, dict) and type(pick.get('slot')) is int, 'Choose each hero by its save slot.')
            _require(pick['slot'] not in seen, 'A hero can hold only one post.')
            seen.add(pick['slot'])
            stance = pick.get('stance', 'roam')
            _require(stance in battle.SIDES + ('roam',), 'A hero holds a wall (north, east, south, west) or roams.')
            profile = next((p for p in self.profiles if p.get('usable') and p.get('room') == room
                            and (p.get('character') or {}).get('slot') == pick['slot']), None)
            _require(profile is not None, f'Hero in slot {pick["slot"] + 1} has no usable calibration in this region: record one there first.')
            if arm:
                _require(not afk.armed_for_hero(st, profile['character']),
                         f"{profile['character']['name']} is already away (an expedition or another post). Claim or cancel it first.")
            chosen.append(dict(slot=pick['slot'], character=dict(profile['character'], class_name=self.class_name(profile['character'])),
                               profile=profile, stance=stance, expedition_id=None))
        return chosen

    def class_name(self, character) -> str | None:
        import panel
        return panel.CLASSES.get((character or {}).get('class'))

    def defense_settings(self, state, args) -> tuple:
        """A siege's region, level, hours and stone budget, checked."""
        c = state['camp']
        room = args.get('room')
        _require(isinstance(room, str) and room.startswith('Act_'), 'Choose a region of the acts.')
        _require(bestiary.region(room), 'The bestiary knows no monster of that region yet: record a calibration there first.')
        level, hours = args.get('level'), args.get('hours')
        _require(type(level) is int and 1 <= level <= defense.MAX_LEVEL, f'Choose a siege level from 1 to {defense.MAX_LEVEL}.')
        _require(isinstance(hours, (int, float)) and not isinstance(hours, bool) and math.isfinite(hours)
                 and defense.MIN_HOURS <= hours <= defense.MAX_HOURS, 'A siege lasts between 15 minutes and 8 hours.')
        stone = args.get('stone', 0)
        _whole(stone, 0, MAX_STONE_BUDGET, 'The stone budget')
        _require(c['resources'].get('stone', 0) >= stone, f"The camp has only {c['resources'].get('stone', 0):,} stone.")
        return room, level, float(hours), stone

    def defense_start(self, state, args, at):
        t = state['town']
        running = t.get('siege') if t.get('siege') and not t['siege'].get('settled') else None
        _require(not running, 'The town is already under siege' + (" (the watch's): stop the watch or let that siege end."
                                                                   if running and running.get('watch') else '.'))
        room, level, hours, stone = self.defense_settings(state, args)
        self.refresh_profiles()
        heroes = self.defense_heroes(state, room, args.get('heroes') or [])
        siege = self.defense_begin(state, room, level, hours, heroes, stone, at)
        self.town_save(state)
        self.log(f"The siege begins: level {level}, {siege['waves_total']} waves from {siege['region']}"
                 + (f", with {', '.join(h['character']['name'] for h in heroes)} on the walls" if heroes else ', towers only') + '.')
        return siege

    def defense_watch(self, state, args, at):
        """Keep the town under siege (towers only), one siege after another, or stop."""
        t = state['town']
        if args.get('off') is True:
            _require(t.get('watch'), 'The town keeps no watch.')
            t['watch'] = None
            self.town_save(state)
            self.log('The watch stands down. A siege under way still runs to its end.')
            return None
        room, level, hours, stone = self.defense_settings(state, args)
        _require(t['towers'], 'The watch needs towers: heroes are stationed only in a siege you start yourself.')
        t['watch'] = dict(room=room, level=level, hours=hours, stone=stone, since=afk.iso(at), started=0, paused=None)
        self.defense_watch_step(state, at)
        self.town_save(state)
        self.log(f"The town keeps watch: level {level} sieges of {defense.duration_text(hours)} from "
                 f"{self.zone_names_map().get(room, room)}, one after another, towers only.")
        return t['watch']

    def defense_watch_step(self, state, at) -> bool:
        """Start the watch's next sieges: back to back from where the last one ended (at most
        a day back), up to WATCH_CATCH_UP at once, each settled at once when already over.
        Pauses (with the reason) instead of failing. True when anything changed."""
        t = state['town']
        watch = t.get('watch')
        if not watch:
            return False
        changed = False
        if watch.get('paused'):
            waiting = sum(1 for h in t['history'] if not h.get('town_collected'))
            if not (watch['paused'].endswith('wait to be collected') and waiting < WATCH_WAITING):
                return False
            watch['paused'] = None               # the shares were collected: the watch goes on by itself
            changed = True
        for _ in range(WATCH_CATCH_UP):
            siege = t.get('siege')
            if siege and not siege.get('settled'):
                break
            waiting = sum(1 for h in t['history'] if not h.get('town_collected'))
            if waiting >= WATCH_WAITING:
                watch['paused'] = f'{waiting} town shares wait to be collected'
                return True
            start = at
            last = t['history'][0] if t['history'] else None
            if siege and siege.get('watch') and last and last.get('id') == siege['id']:
                start = max(afk.parse_iso(last['ended_at']), at - timedelta(hours=WATCH_LOOKBACK_HOURS))
            if not t['towers']:
                watch['paused'] = 'the town has no towers'
                return True
            stone = min(watch['stone'], state['camp']['resources'].get('stone', 0))
            try:
                self.defense_begin(state, watch['room'], watch['level'], watch['hours'], [], stone, start, watch=True)
            except (ValueError, SystemExit) as error:
                watch['paused'] = str(error)
                return True
            watch['started'] += 1
            changed = True
            if not self.defense_settle(state, at, True):
                break
            if t['history'] and t['history'][0].get('outcome') == 'fell':
                watch['paused'] = 'the keep fell in the last siege: lower the level or strengthen the town, then keep watch again'
                return True
        return changed

    def defense_begin(self, state, room, level, hours, heroes, stone, at, watch=False) -> dict:
        """Draw a siege, write its record (and its heroes' plans and state.json entries in one
        write), take its stone and point the town at it. Nothing is saved to workers.json here."""
        t, c = state['town'], state['camp']
        entries = bestiary.region(room)
        _require(entries, 'The bestiary knows no monster of that region yet: record a calibration there first.')
        ident = 'defense_' + at.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
        for h in heroes:
            h['expedition_id'] = f"{ident}_h{h['slot']}"
        snap = defense.snapshot(t, c['buildings'])
        walls_now = {s: town.wall_now(t, c, s, at) for s in battle.SIDES}
        record = defense.new_record(ident, room, level, float(hours), town_snapshot=snap, walls_now=walls_now,
                                    keep_now=town.keep_now(t, c, at), heroes=heroes, entries=entries,
                                    goblins=(worker_loot.pools().get('goblins', {}).get(room) or {}), stone_budget=stone,
                                    build=bestiary.current_build(), at=at, region_name=self.zone_names_map().get(room, room))
        record['watch'] = bool(watch)
        path = self.data / 'plans' / f'{ident}.json'
        plans = [self.defense_hero_plan(record, path, h) for h in heroes]
        with recovery.data_lock(self.data):
            afk.write_json(path, record)
            for p in plans:
                afk.write_json(self.data / 'plans' / f"{p['expedition_id']}.json", p)
            st = afk.load_state(self.data / 'state.json')
            for p in plans:
                _require(not afk.armed_for_hero(st, p['character']), f"{p['character']['name']} left for something else meanwhile.")
            for p in plans:
                st['expeditions'][p['expedition_id']] = dict(expedition_id=p['expedition_id'], plan=str(self.data / 'plans' / f"{p['expedition_id']}.json"),
                                                            started_at=record['started_at'], hours=record['hours'], hero=afk.hero_key(p['character']),
                                                            mode='defense')
            afk.save_state(st, self.data / 'state.json')
        c['resources']['stone'] -= stone
        t['siege'] = dict(id=ident, path=str(path), room=room, level=level, started_at=record['started_at'], hours=record['hours'],
                          heroes=[h['slot'] for h in heroes], settled=False, watch=bool(watch))
        return dict(t['siege'], waves_total=record['waves_total'], region=record['region'])

    def defense_hero_plan(self, record, record_path, hero) -> dict:
        """A stationed hero's expedition plan: its claim is its share of the siege, built when the siege ends."""
        profile, room = hero['profile'], record['room']
        plan = afk.make_plan(record['hours'], [(room, 1)], hero['expedition_id'], 40, 'pickup', profile_overrides={room: profile})
        kills = sorted({q['hash'] for q in profile['packets'] if q.get('kind') == 'kill' and afk.replayable(q)})
        by_hash = {q['hash']: q for q in profile['packets']}
        plan['packets'] = [dict(hash=h, count=1, monster_key=by_hash[h].get('monster_key', ''), room=room, kind='kill', exp=by_hash[h].get('exp'))
                           for h in kills]
        reward_modifiers.apply_to_plan(plan, profile, reward_modifiers.load(self.data / 'reward-modifiers.json'))
        mods = plan['reward_modifiers']
        mods['magic_find'] = min(100.0, mods['magic_find'] * record['mf_bonus'])
        plan['effective_magic_find'] = float((profile.get('reward_baseline') or {}).get('magic_find') or 0) * mods['magic_find']
        plan['defense'] = dict(id=record['id'], record=str(record_path), slot=hero['slot'],
                               candidates=[dict(p, weight=float(by_hash[p['hash']].get('count') or 1)) for p in plan['packets']])
        for p in plan['defense']['candidates']:
            p.pop('count', None)
        plan['packets'] = []
        plan['mode'] = 'defense'
        plan['farm_context'] = profile.get('farm_context')
        import panel
        plan['panel_version'] = panel.VERSION
        region = self.zone_names_map().get(room, room)
        plan.update(label=f"Defense L{record['level']} · " + panel.expedition_label(room, record['hours'], hero['character']['name']),
                    label_hero=hero['character']['name'], label_region=region)
        afk.rebuild_preview(plan)
        return plan

    def defense_settle(self, state, at, persist=False) -> bool:
        """A siege that is over: its walls, spoils, stone and records reach the town, once
        (the records file and the note only when ``persist``). True when it settled now."""
        siege = state['town'].get('siege')
        if not siege or siege.get('settled'):
            return False
        watch = state['town'].get('watch')
        try:
            record = self.defense_record(siege)
        except ValueError:
            return False
        if not defense.is_over(record, at):
            return False
        last = end = defense.end_wave(record)
        rows = record['timeline'][:end]
        final = rows[-1]['after'] if rows else record['start_state']
        town.set_health(state['town'], final['walls'], final['keep'], defense.ends_at(record))
        stone_back = int(final.get('stone', record['stone_budget'])) if rows else int(record['stone_budget'])
        camp.give_back(state['camp'], dict(stone=stone_back))
        kept = camp.add(state['camp'], dict(spoils=defense.spoils(rows)))
        for r in rows:
            for g in r['groups']:
                if g.get('entry') and g['killed']:
                    state['town']['slain'][g['entry']] = state['town']['slain'].get(g['entry'], 0) + g['killed']
        new = defense.record_result(self.data, record) if persist else dict(held=False, waves=False)
        outcome = defense.outcome(record, at)
        entry = dict(id=record['id'], path=siege['path'], room=record['room'], region=record['region'], level=record['level'],
                     outcome=outcome, waves=last, waves_total=record['waves_total'], kills=sum(r['kills'] for r in rows),
                     spoils=kept.get('spoils', 0), stone_back=stone_back, ended_at=afk.iso(defense.ends_at(record)), record=new,
                     town_collected=False)
        entry['watch'] = bool(record.get('watch'))
        if not defense.shares(record)['town']:
            entry['town_collected'] = True        # nothing for the town to replay: no trip needed
        state['town']['history'] = town.keep_history(state['town']['history'], entry)
        if watch and outcome == 'fell' and record.get('watch') and not watch.get('paused'):
            watch['paused'] = 'the keep fell in the last siege: lower the level or strengthen the town, then keep watch again'
        siege['settled'] = True
        if persist:
            self.note(f"The siege of level {record['level']} {'held' if outcome == 'held' else 'fell' if outcome == 'fell' else 'ended in a retreat'} "
                      f"after {last} waves: {entry['kills']:,} monsters slain, +{entry['spoils']:,} spoils.")
        return True

    def defense_collect(self, ident=None, every=False):
        """The town's share of a finished siege: its monsters' packets replayed through the game
        in the siege's region by any offline hero standing there, no experience; then the Vault.
        ``every``: every waiting share of the region the hero stands in, one after another."""
        if every:
            s = self.fresh()
            waiting = [h['id'] for h in self.town_load()['town']['history'] if not h.get('town_collected') and h['room'] == s['room']]
            _require(waiting, 'No town share waits in the region your hero stands in.')
            for ident in reversed(waiting):      # the oldest first
                self.defense_collect(ident)
            return len(waiting)
        state = self.town_load()
        entry = next((h for h in state['town']['history'] if (ident is None or h['id'] == ident) and not h.get('town_collected')), None)
        _require(entry, 'No finished siege is waiting for its town share.')
        record = self.defense_record(dict(id=entry['id'], path=entry['path']))
        import panel
        place = f"{self.zone_names_map().get(record['room'], record['room'])} ({record['room']})"
        claim = record.get('town_claim') or {}
        if claim.get('plan'):
            path = Path(claim['plan'])
            plan = panel.read(path, {}) or {}
        else:
            s = self.fresh()
            prefs = panel.load_preferences(self.data)
            plan = defense.town_plan(record, s['character'], 'AFK · Town · Siege L' + str(record['level']) + ' · ' + datetime.now().strftime('%Y-%m-%d'),
                                     prefs['filtered_items'], prefs['delivery_speed'])
            path = self.data / 'plans' / f"{plan['expedition_id']}.json"
            with recovery.data_lock(self.data):
                afk.write_json(path, plan)
                record['town_claim'] = dict(plan=str(path), delivery_id=plan['expedition_id'], planned_at=plan['planned_at'])
                afk.write_json(Path(entry['path']), record)
        ident_plan = plan['expedition_id']
        result_path = self.data / 'sessions' / f'{ident_plan}.result.json'
        if plan['packets'] and not (panel.read(result_path, {}) or {}).get('rewards_saved'):
            s = self.fresh()
            _require(not s['replay_running'], 'Wait for reward delivery to finish.')
            _require(s['room'] == record['room'], f"Load any offline hero in {place} to collect the town's share: its loot replays there.")
            if not (self.data / 'sessions' / f'{ident_plan}.progress.json').exists() and not panel.same_character(plan['character'], s['character']):
                plan['character'] = s['character']
                afk.write_json(path, plan)
            self.log(f"Delivering the town's share of the siege through the game in {place}...")
            self.cli('worker-replay', path)
        result = panel.read(result_path, {}) or {}
        _require(not plan['packets'] or result.get('rewards_saved'), 'The town share was not delivered completely; collect again to continue.')
        with recovery.data_lock(self.data):
            record = self.defense_record(dict(id=entry['id'], path=entry['path']))
            record['town_claim'] = dict(record.get('town_claim') or {}, collected_at=afk.iso(afk.now_utc()))
            afk.write_json(Path(entry['path']), record)
        state = self.town_load()
        for h in state['town']['history']:
            if h['id'] == entry['id']:
                h['town_collected'] = True
        self.town_save(state)
        self.log(f"The town's share of the siege is delivered: {sum(p['count'] for p in plan['packets']):,} replays.")
        return result
