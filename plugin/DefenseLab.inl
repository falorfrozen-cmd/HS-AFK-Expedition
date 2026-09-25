// Research R2 (Stronghold design, 2026-09-26): the live-defense lab.
//
// Questions it answers in a running offline game:
// - Does a monster created directly (instance_create_depth) come alive with
//   the right health, rank, affixes and behaviour, in town and in a zone?
// - Can a "tower" kill it through the game's own protected-value store, so that
//   the game itself runs the death and the drops, with the experience credited
//   through the game's EnemyGiveExperience (as the reward replays do)?
// - Do tower kills drop as often as the hero's kills?
// - What do 60 and 120 live monsters near the player cost per frame?
//
// Commands (`afk def ...`), research build only:
//   def player
//   def spawn <Object> [count=1] [radius=450] [rank=0] [aggro=N] [affix=a,b,...]
//   def list [max=12]
//   def var <index> <variable>
//   def set <index|all> <variable> <number|'text|true|false>
//   def hit <index|all> <amount|pct%>
//   def tower start <pct-of-max-hp> [range=350] [period=30] [xp=1] [fx=1] | tower stop
//   def gate <radius> | gate off        (a monster that reaches it has broken through)
//   def stats                           (deaths, drops and experience by who killed)
//   def perf start | show | stop
//   def clear
//
// Nothing here runs unless a `def` command asks for it. Game thread only
// (commands, the frame callback and the DropItem hook all run there).
namespace DefenseLab {

struct Spawned { RValue id; std::string object; int rank; bool tower = false; bool breached = false; };
static std::vector<Spawned> g_Monsters;
static uint64_t g_DropsAtFirstSpawn = 0;

struct TowerState {
    bool on = false; double pct = 0; double range = 350; int period = 30; int frame = 0; bool xp = true; bool fx = true;
    double ax = 0, ay = 0; int hits = 0; int kills = 0; int failed = 0; int zeroSkipped = 0;
};
static TowerState g_Tower;

struct GateState { bool on = false; double radius = 0; double ax = 0, ay = 0; };
static GateState g_Gate;

struct Stats {
    uint64_t towerDrops = 0, otherDrops = 0;
    std::string towerArgs, otherArgs;
    int xpCalls = 0, xpFailed = 0; double xpSum = 0;
    int breaches = 0, fxMade = 0, fxFailed = 0;
};
static Stats g_Stats;

struct PerfState { bool on = false; std::vector<double> ms; std::chrono::steady_clock::time_point last{}; bool primed = false; };
static PerfState g_Perf;

static std::string Fixed(double v, int decimals = 0)
{
    if (!std::isfinite(v)) return "?";
    char b[64]; std::snprintf(b, sizeof b, "%.*f", decimals, v); return b;
}

static bool Alive(const RValue& id)
{
    try { return g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean(); } catch (...) { return false; }
}

static bool PlayerAt(RValue& id, double& x, double& y)
{
    RValue pobj;
    if (!ObjectIndex("Player_obj", pobj)) return false;
    try {
        if (g_Yytk->CallBuiltin("instance_number", { pobj }).ToDouble() < 1) return false;
        id = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
    } catch (...) { return false; }
    x = GetVarNumber(id, "x", NAN); y = GetVarNumber(id, "y", NAN);
    return std::isfinite(x) && std::isfinite(y);
}

// A protected combat value (enemy_hp, max_hp, damage, killExperience): the
// variable holds a handle into the game's store, read and written through the
// game's own wrappers.
static bool ProtectedNumber(const RValue& id, const char* name, RValue& handle, double& value)
{
    handle = GetVar(id, name);
    if (!IsNumberKind(handle) || handle.m_Kind == VALUE_BOOL) return false;
    try {
        RValue r = g_Yytk->CallGameScript("gml_Script_PC_GetVariableGMLWrapper", { handle });
        if (!IsNumberKind(r)) return false;
        value = r.ToDouble();
        return std::isfinite(value);
    } catch (...) { return false; }
}

static bool SetProtected(const RValue& handle, double value)
{
    try { g_Yytk->CallGameScript("gml_Script_PC_SetVariableGMLWrapper", { handle, RValue(value) }); return true; }
    catch (...) { return false; }
}

static std::vector<std::string> Tokens(std::istream& in, const std::string& first)
{
    std::vector<std::string> t;
    if (!first.empty()) t.push_back(first);
    for (std::string w; in >> w;) t.push_back(w);
    return t;
}

static bool ParseNumber(const std::string& text, double& out)
{
    if (text.empty()) return false;
    char* end = nullptr;
    const double v = std::strtod(text.c_str(), &end);
    if (!end || *end != '\0' || !std::isfinite(v)) return false;
    out = v; return true;
}

static bool Number(const std::vector<std::string>& t, size_t i, double& out)
{
    return i < t.size() && t[i].find('=') == std::string::npos && ParseNumber(t[i], out);
}

// key=value options anywhere in the arguments
static bool Option(const std::vector<std::string>& t, const std::string& key, std::string& out)
{
    for (const auto& w : t) if (w.size() > key.size() + 1 && w.compare(0, key.size() + 1, key + "=") == 0) { out = w.substr(key.size() + 1); return true; }
    return false;
}

static RValue ValueOf(const std::string& text)
{
    if (text == "true") return RValue(true);
    if (text == "false") return RValue(false);
    if (text.size() > 1 && text[0] == (char)0x27) return RValue(text.substr(1));
    double v = 0; ParseNumber(text, v); return RValue(v);
}

static void Player()
{
    RValue pid; double x, y;
    if (!PlayerAt(pid, x, y)) { Out("def player: no player in the room " + CurrentRoomName()); return; }
    Out("def player: " + Fixed(x) + "," + Fixed(y) + " depth " + Stringify(GetVar(pid, "depth")) + " room " + CurrentRoomName());
}

// Affixes before Alarm_4 builds the monster's stats: the id list and the flag
// array the natural spawner fills (MEASURED 2026-09-26: a natural Frozen
// Champion carried affixList [16] and enemyAffix[16] = true).
static bool ApplyAffixes(const RValue& id, const std::vector<int>& affixes)
{
    if (affixes.empty()) return true;
    try {
        RValue list = g_Yytk->CallBuiltin("array_create", { RValue((double)affixes.size()), RValue(0.0) });
        for (size_t i = 0; i < affixes.size(); ++i) g_Yytk->CallBuiltin("array_set", { list, RValue((double)i), RValue((double)affixes[i]) });
        g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("affixList"), list });
        RValue flags = GetVar(id, "enemyAffix");
        if (flags.m_Kind != VALUE_ARRAY) flags = g_Yytk->CallBuiltin("array_create", { RValue(64.0), RValue(0.0) });
        for (int a : affixes) if (a >= 0 && a < 64) g_Yytk->CallBuiltin("array_set", { flags, RValue((double)a), RValue(true) });
        g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("enemyAffix"), flags });
        return true;
    } catch (...) { return false; }
}

static void Spawn(const std::vector<std::string>& t)
{
    if (t.empty()) { Out("def spawn: usage -> def spawn <Object> [count=1] [radius=450] [rank=0] [aggro=N] [affix=a,b]"); return; }
    double count = 1, radius = 450, rank = 0, aggro = -1;
    Number(t, 1, count); Number(t, 2, radius); Number(t, 3, rank);
    std::string opt;
    if (Option(t, "aggro", opt)) ParseNumber(opt, aggro);
    std::vector<int> affixes;
    if (Option(t, "affix", opt)) {
        std::stringstream parts(opt); std::string part;
        while (std::getline(parts, part, ',')) { double a; if (ParseNumber(part, a) && a >= 0 && a < 64) affixes.push_back((int)a); }
    }
    int n = (int)count; if (n < 1) n = 1; if (n > 200) n = 200;
    RValue oi;
    if (!ObjectIndex(t[0], oi)) { Out("def spawn: no object named " + t[0]); return; }
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { Out("def spawn: no player in the room"); return; }
    RValue depth = GetVar(pid, "depth");
    if (!IsNumberKind(depth)) depth = RValue(0.0);
    if (g_Monsters.empty()) { g_DropsAtFirstSpawn = g_DropItemCalls.load(); g_Stats = Stats{}; }
    int made = 0, affixFailed = 0;
    for (int i = 0; i < n; ++i) {
        const double a = 6.283185307179586 * (i + 0.5) / n;
        RValue id;
        try { id = g_Yytk->CallBuiltin("instance_create_depth", { RValue(px + radius * std::cos(a)), RValue(py + radius * std::sin(a)), depth, oi }); }
        catch (...) { Out("def spawn: instance_create_depth threw at " + std::to_string(i)); break; }
        if (!Alive(id)) { Out("def spawn: instance " + std::to_string(i) + " did not survive its creation"); continue; }
        try {
            g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("afkLab"), RValue(1.0) });
            if (rank >= 1) g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("enemyRarity"), RValue(rank) });
            if (aggro > 0) {
                g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("aggroRange"), RValue(aggro) });
                g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("distance"), RValue(aggro) });
            }
        } catch (...) {}
        if (!ApplyAffixes(id, affixes)) ++affixFailed;
        g_Monsters.push_back({ id, t[0], (int)rank });
        ++made;
    }
    std::string affixText;
    for (size_t i = 0; i < affixes.size(); ++i) affixText += (i ? "," : "") + std::to_string(affixes[i]);
    Out("def spawn: " + std::to_string(made) + " x " + t[0] + (rank >= 1 ? " rank " + Fixed(rank) : "") + (aggro > 0 ? " aggro " + Fixed(aggro) : "")
        + (affixes.empty() ? "" : " affixes " + affixText + (affixFailed ? " (" + std::to_string(affixFailed) + " failed)" : ""))
        + " at radius " + Fixed(radius) + " around " + Fixed(px) + "," + Fixed(py) + " in " + CurrentRoomName() + "; tracking " + std::to_string(g_Monsters.size()));
}

static int AliveCount()
{
    int n = 0;
    for (const auto& m : g_Monsters) if (Alive(m.id)) ++n;
    return n;
}

static void List(const std::vector<std::string>& t)
{
    double max = 12; Number(t, 0, max);
    RValue pid; double px = NAN, py = NAN; PlayerAt(pid, px, py);
    int alive = 0, shown = 0;
    for (size_t i = 0; i < g_Monsters.size(); ++i) {
        const auto& m = g_Monsters[i];
        if (!Alive(m.id)) continue;
        ++alive;
        if (shown >= (int)max) continue;
        ++shown;
        const double x = GetVarNumber(m.id, "x", NAN), y = GetVarNumber(m.id, "y", NAN);
        RValue h; double hp = NAN, mx = NAN, dmg = NAN;
        ProtectedNumber(m.id, "enemy_hp", h, hp);
        ProtectedNumber(m.id, "max_hp", h, mx);
        ProtectedNumber(m.id, "damage", h, dmg);
        Out("  #" + std::to_string(i) + " " + m.object + " at " + Fixed(x) + "," + Fixed(y) + " dist " + Fixed(std::hypot(x - px, y - py))
            + " rarity " + Stringify(GetVar(m.id, "enemyRarity")) + " hp " + Fixed(hp) + "/" + Fixed(mx) + " dmg " + Fixed(dmg)
            + " speed " + Stringify(GetVar(m.id, "moveSpeed")) + " affixes " + Stringify(GetVar(m.id, "affixList")).substr(0, 80));
    }
    Out("def list: " + std::to_string(alive) + " alive of " + std::to_string(g_Monsters.size()) + " spawned; drop calls since the first spawn "
        + std::to_string(g_DropItemCalls.load() - g_DropsAtFirstSpawn) + "; tower hits " + std::to_string(g_Tower.hits) + ", kills "
        + std::to_string(g_Tower.kills) + ", zero skipped " + std::to_string(g_Tower.zeroSkipped) + "; room " + CurrentRoomName()
        + "; player " + Fixed(px) + "," + Fixed(py));
}

static void Var(const std::vector<std::string>& t)
{
    double index = -1;
    if (t.size() < 2 || !Number(t, 0, index) || index < 0 || index >= (double)g_Monsters.size()) {
        Out("def var: usage -> def var <index> <variable>"); return;
    }
    const auto& m = g_Monsters[(size_t)index];
    if (!Alive(m.id)) { Out("def var: #" + t[0] + " is gone"); return; }
    RValue h; double v;
    const bool prot = ProtectedNumber(m.id, t[1].c_str(), h, v) && v != 0;
    Out("def var #" + t[0] + "." + t[1] + " = " + Stringify(GetVar(m.id, t[1].c_str())).substr(0, 300) + (prot ? " (protected: " + Fixed(v, 2) + ")" : ""));
}

static void Set(const std::vector<std::string>& t)
{
    if (t.size() < 3) { Out("def set: usage -> def set <index|all> <variable> <number|'text|true|false>"); return; }
    int changed = 0;
    for (size_t i = 0; i < g_Monsters.size(); ++i) {
        if (t[0] != "all" && t[0] != std::to_string(i)) continue;
        if (!Alive(g_Monsters[i].id)) continue;
        try { g_Yytk->CallBuiltin("variable_instance_set", { g_Monsters[i].id, RValue(t[1]), ValueOf(t[2]) }); ++changed; } catch (...) {}
    }
    Out("def set: " + t[1] + " = " + t[2] + " on " + std::to_string(changed) + " monsters");
}

// The monster's own experience, handed to the hero through the game's routine
// with self = the monster and other = the player (what the reward replays do).
static void CreditExperience(const Spawned& m)
{
    RValue h; double amount = 0;
    if (!ProtectedNumber(m.id, "killExperience", h, amount) || !(amount > 0)) { ++g_Stats.xpFailed; return; }
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { ++g_Stats.xpFailed; return; }
    CInstance* self = ResolveInstance(m.id);
    CInstance* player = ResolveInstance(pid);
    if (!self || !player) { ++g_Stats.xpFailed; return; }
    try {
        RValue res;
        const AurieStatus st = g_Yytk->CallGameScriptEx(res, "gml_Script_EnemyGiveExperience", self, player, { RValue(amount) });
        if (AurieSuccess(st)) { ++g_Stats.xpCalls; g_Stats.xpSum += amount; } else ++g_Stats.xpFailed;
    } catch (...) { ++g_Stats.xpFailed; }
}

// A built-in spark where the tower hits (visual only; the real towers will use
// the game's projectile sprites).
static void Effect(double x, double y, double depth)
{
    try {
        g_Yytk->CallBuiltin("effect_create_depth", { RValue(depth), RValue(7.0), RValue(x), RValue(y), RValue(1.0), RValue(16769024.0) });
        ++g_Stats.fxMade;
    } catch (...) { ++g_Stats.fxFailed; }
}

// One hit: pct of max health, or a flat amount. A killing hit credits the
// monster's experience first (optional) and marks the monster as a tower kill.
static bool Hit(Spawned& m, double amount, bool pct, bool creditXp, bool& killed)
{
    RValue hHp, hMax; double hp = 0, mx = 0;
    if (!ProtectedNumber(m.id, "enemy_hp", hHp, hp)) return false;
    if (!ProtectedNumber(m.id, "max_hp", hMax, mx) || mx <= 0) mx = hp;
    const double damage = pct ? mx * amount / 100.0 : amount;
    const double next = hp - damage > 0 ? hp - damage : 0.0;
    killed = next <= 0;
    if (killed) {
        m.tower = true;
        try { g_Yytk->CallBuiltin("variable_instance_set", { m.id, RValue("afkLabTower"), RValue(1.0) }); } catch (...) {}
        if (creditXp) CreditExperience(m);
    }
    return SetProtected(hHp, next);
}

static void HitCommand(const std::vector<std::string>& t)
{
    if (t.size() < 2) { Out("def hit: usage -> def hit <index|all> <amount|pct%>"); return; }
    std::string amountText = t[1];
    const bool pct = !amountText.empty() && amountText.back() == '%';
    if (pct) amountText.pop_back();
    double amount = 0;
    if (!ParseNumber(amountText, amount) || !(amount > 0)) { Out("def hit: the amount must be a positive number or a percentage"); return; }
    int hits = 0, killed = 0, failed = 0;
    for (size_t i = 0; i < g_Monsters.size(); ++i) {
        if (t[0] != "all" && t[0] != std::to_string(i)) continue;
        if (!Alive(g_Monsters[i].id)) continue;
        bool k = false;
        if (Hit(g_Monsters[i], amount, pct, true, k)) { ++hits; if (k) ++killed; } else ++failed;
    }
    Out("def hit: " + std::to_string(hits) + " hit, " + std::to_string(killed) + " brought to zero, " + std::to_string(failed) + " failed");
}

static void TowerCommand(const std::vector<std::string>& t)
{
    if (!t.empty() && t[0] == "stop") { g_Tower.on = false; Out("def tower: stopped after " + std::to_string(g_Tower.hits) + " hits, " + std::to_string(g_Tower.kills) + " kills"); return; }
    double pct = 0, range = 350, period = 30;
    if (t.empty() || t[0] != "start" || !Number(t, 1, pct) || !(pct > 0)) {
        Out("def tower: usage -> def tower start <pct-of-max-hp> [range=350] [period=30] [xp=1] [fx=1] | def tower stop"); return;
    }
    Number(t, 2, range); Number(t, 3, period);
    std::string opt;
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { Out("def tower: no player to anchor the tower on"); return; }
    g_Tower = TowerState{};
    g_Tower.on = true; g_Tower.pct = pct; g_Tower.range = range; g_Tower.period = period < 1 ? 1 : (int)period; g_Tower.ax = px; g_Tower.ay = py;
    if (Option(t, "xp", opt)) g_Tower.xp = opt != "0";
    if (Option(t, "fx", opt)) g_Tower.fx = opt != "0";
    Out("def tower: on at " + Fixed(px) + "," + Fixed(py) + ", " + Fixed(pct, 1) + "% of max health every " + std::to_string(g_Tower.period)
        + " frames, range " + Fixed(range) + ", experience " + (g_Tower.xp ? "credited" : "off") + ", sparks " + (g_Tower.fx ? "on" : "off"));
}

static void TowerTick()
{
    if (!g_Tower.on || ++g_Tower.frame < g_Tower.period) return;
    g_Tower.frame = 0;
    Spawned* best = nullptr; double bestDistance = 1e18, bx = 0, by = 0;
    for (auto& m : g_Monsters) {
        if (m.tower || m.breached || !Alive(m.id)) continue;
        const double x = GetVarNumber(m.id, "x", NAN), y = GetVarNumber(m.id, "y", NAN);
        const double d = std::hypot(x - g_Tower.ax, y - g_Tower.ay);
        if (!std::isfinite(d) || d > g_Tower.range || d >= bestDistance) continue;
        RValue h; double hp = 0;
        if (ProtectedNumber(m.id, "enemy_hp", h, hp) && hp <= 0) { ++g_Tower.zeroSkipped; continue; }
        bestDistance = d; best = &m; bx = x; by = y;
    }
    if (!best) return;
    bool killed = false;
    if (Hit(*best, g_Tower.pct, true, g_Tower.xp, killed)) {
        ++g_Tower.hits; if (killed) ++g_Tower.kills;
        if (g_Tower.fx) Effect(bx, by, GetVarNumber(best->id, "depth", 0.0) - 1);
    } else ++g_Tower.failed;
}

static void GateCommand(const std::vector<std::string>& t)
{
    if (!t.empty() && t[0] == "off") { g_Gate.on = false; Out("def gate: off after " + std::to_string(g_Stats.breaches) + " breaches"); return; }
    double radius = 0;
    if (!Number(t, 0, radius) || !(radius > 0)) { Out("def gate: usage -> def gate <radius> | def gate off"); return; }
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { Out("def gate: no player to anchor the gate on"); return; }
    g_Gate = GateState{ true, radius, px, py };
    Out("def gate: a monster within " + Fixed(radius) + " of " + Fixed(px) + "," + Fixed(py) + " breaks through (removed, no drops)");
}

static void GateTick()
{
    if (!g_Gate.on) return;
    for (auto& m : g_Monsters) {
        if (m.breached || m.tower || !Alive(m.id)) continue;
        const double x = GetVarNumber(m.id, "x", NAN), y = GetVarNumber(m.id, "y", NAN);
        if (std::hypot(x - g_Gate.ax, y - g_Gate.ay) > g_Gate.radius) continue;
        m.breached = true; ++g_Stats.breaches;
        try { g_Yytk->CallBuiltin("instance_destroy", { m.id, RValue(false) }); } catch (...) {}
    }
}

static void StatsCommand()
{
    int alive = 0, towerDead = 0, otherDead = 0, breached = 0;
    for (const auto& m : g_Monsters) {
        if (Alive(m.id)) { ++alive; continue; }
        if (m.breached) ++breached; else if (m.tower) ++towerDead; else ++otherDead;
    }
    auto rate = [](uint64_t drops, int deaths) { return deaths > 0 ? Fixed(100.0 * (double)drops / deaths, 1) + "%" : std::string("-"); };
    Out("def stats: tracked " + std::to_string(g_Monsters.size()) + ", alive " + std::to_string(alive) + ", breached " + std::to_string(breached));
    Out("  tower kills: " + std::to_string(towerDead) + " deaths, " + std::to_string(g_Stats.towerDrops) + " DropItem calls (" + rate(g_Stats.towerDrops, towerDead) + ")"
        + "; experience credited " + std::to_string(g_Stats.xpCalls) + " times, sum " + Fixed(g_Stats.xpSum) + ", failed " + std::to_string(g_Stats.xpFailed));
    Out("  other kills (the hero): " + std::to_string(otherDead) + " deaths, " + std::to_string(g_Stats.otherDrops) + " DropItem calls (" + rate(g_Stats.otherDrops, otherDead) + ")");
    Out("  first tower drop args: " + (g_Stats.towerArgs.empty() ? std::string("-") : g_Stats.towerArgs));
    Out("  first hero drop args:  " + (g_Stats.otherArgs.empty() ? std::string("-") : g_Stats.otherArgs));
    Out("  sparks made " + std::to_string(g_Stats.fxMade) + ", failed " + std::to_string(g_Stats.fxFailed));
}

// Called from the DropItem hook for every drop call: counts the lab's own
// monsters by who killed them, and keeps the first call's arguments of each.
static void OnDropItem(CInstance* self, int argc, RValue** args)
{
    if (g_Monsters.empty() || !self) return;
    try {
        const RValue me = self->ToRValue();
        if (!IsNumberKind(GetVar(me, "afkLab"))) return;
        const bool tower = IsNumberKind(GetVar(me, "afkLabTower"));
        (tower ? g_Stats.towerDrops : g_Stats.otherDrops)++;
        std::string& sample = tower ? g_Stats.towerArgs : g_Stats.otherArgs;
        if (sample.empty())
            for (int i = 0; i < argc && i < 12; ++i) sample += (i ? " | " : "") + (args && args[i] ? Stringify(*args[i]).substr(0, 28) : std::string("?"));
    } catch (...) {}
}

static void PerfTick()
{
    if (!g_Perf.on) return;
    const auto now = std::chrono::steady_clock::now();
    if (g_Perf.primed && g_Perf.ms.size() < 200000)
        g_Perf.ms.push_back(std::chrono::duration<double, std::milli>(now - g_Perf.last).count());
    g_Perf.last = now; g_Perf.primed = true;
}

static void PerfShow()
{
    if (g_Perf.ms.empty()) { Out("def perf: no frames timed yet"); return; }
    std::vector<double> v = g_Perf.ms;
    std::sort(v.begin(), v.end());
    double sum = 0; for (double x : v) sum += x;
    const double avg = sum / (double)v.size();
    const double p95 = v[(size_t)(0.95 * (double)(v.size() - 1))];
    double enemies = -1;
    RValue eobj;
    if (ObjectIndex("Enemy_Parent_obj", eobj)) { try { enemies = g_Yytk->CallBuiltin("instance_number", { eobj }).ToDouble(); } catch (...) {} }
    Out("def perf: " + std::to_string(v.size()) + " frames, avg " + Fixed(avg, 2) + " ms, p95 " + Fixed(p95, 2) + " ms, max " + Fixed(v.back(), 2)
        + " ms; Enemy_Parent_obj " + Fixed(enemies) + ", ours alive " + std::to_string(AliveCount()) + "; room " + CurrentRoomName());
}

static void PerfCommand(const std::vector<std::string>& t)
{
    const std::string w = t.empty() ? "show" : t[0];
    if (w == "start") { g_Perf = PerfState{}; g_Perf.on = true; Out("def perf: timing every frame"); return; }
    if (w == "stop") { PerfShow(); g_Perf.on = false; return; }
    PerfShow();
}

static void Clear()
{
    int destroyed = 0;
    for (const auto& m : g_Monsters) {
        if (!Alive(m.id)) continue;
        try { g_Yytk->CallBuiltin("instance_destroy", { m.id, RValue(false) }); ++destroyed; } catch (...) {}
    }
    g_Monsters.clear(); g_Tower = TowerState{}; g_Gate = GateState{};
    Out("def clear: removed " + std::to_string(destroyed) + " live monsters (no Destroy event)");
}

static void Command(const std::string& verb, const std::string& first, std::istream& rest)
{
    const std::vector<std::string> t = Tokens(rest, first);
    try {
        if (verb == "player") return Player();
        if (verb == "spawn") return Spawn(t);
        if (verb == "list") return List(t);
        if (verb == "var") return Var(t);
        if (verb == "set") return Set(t);
        if (verb == "hit") return HitCommand(t);
        if (verb == "tower") return TowerCommand(t);
        if (verb == "gate") return GateCommand(t);
        if (verb == "stats") return StatsCommand();
        if (verb == "perf") return PerfCommand(t);
        if (verb == "clear") return Clear();
    } catch (...) { Out("def " + verb + ": EXCEPTION"); return; }
    Out("def: usage -> def player | spawn | list | var | set | hit | tower | gate | stats | perf | clear");
}

static void Frame()
{
    PerfTick();
    if (g_Tower.on) { try { TowerTick(); } catch (...) { ++g_Tower.failed; } }
    if (g_Gate.on) { try { GateTick(); } catch (...) {} }
}

} // namespace DefenseLab
