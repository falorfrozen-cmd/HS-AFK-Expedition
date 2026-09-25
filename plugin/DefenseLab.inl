// Research R2 (Stronghold design, 2026-09-26): the live-defense lab.
//
// Questions it answers in a running offline game:
// - Does a monster created directly (instance_create_depth) come alive with
//   the right health, rank and behaviour, in town and in a zone?
// - Can a "tower" hit it through the game's own protected-value store, so that
//   the game itself runs the death, the drops and the experience?
// - What do 60 and 120 live monsters near the player cost per frame?
//
// Commands (`afk def ...`), research build only:
//   def player
//   def spawn <Object> [count=1] [radius=450] [rank=0]
//   def list [max=12]
//   def var <index> <variable>
//   def hit <index|all> <amount|pct%>
//   def tower start <pct-of-max-hp> [range=350] [period-frames=30] | tower stop
//   def perf start | show | stop
//   def clear
//
// Nothing here runs unless a `def` command asks for it. Game thread only
// (commands and the frame callback both run there).
namespace DefenseLab {

struct Spawned { RValue id; std::string object; int rank; };
static std::vector<Spawned> g_Monsters;
static uint64_t g_DropsAtFirstSpawn = 0;

struct TowerState {
    bool on = false; double pct = 0; double range = 350; int period = 30; int frame = 0;
    double ax = 0, ay = 0; int hits = 0; int kills = 0; int failed = 0;
};
static TowerState g_Tower;

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

// A protected combat value (enemy_hp, max_hp, damage): the variable holds a
// handle into the game's store, read and written through the game's own wrappers.
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

static bool Number(const std::vector<std::string>& t, size_t i, double& out)
{
    if (i >= t.size()) return false;
    char* end = nullptr;
    const double v = std::strtod(t[i].c_str(), &end);
    if (!end || *end != '\0' || !std::isfinite(v)) return false;
    out = v; return true;
}

static void Player()
{
    RValue pid; double x, y;
    if (!PlayerAt(pid, x, y)) { Out("def player: no player in the room " + CurrentRoomName()); return; }
    Out("def player: " + Fixed(x) + "," + Fixed(y) + " depth " + Stringify(GetVar(pid, "depth")) + " room " + CurrentRoomName());
}

static void Spawn(const std::vector<std::string>& t)
{
    if (t.empty()) { Out("def spawn: usage -> def spawn <Object> [count=1] [radius=450] [rank=0]"); return; }
    double count = 1, radius = 450, rank = 0;
    Number(t, 1, count); Number(t, 2, radius); Number(t, 3, rank);
    int n = (int)count; if (n < 1) n = 1; if (n > 200) n = 200;
    RValue oi;
    if (!ObjectIndex(t[0], oi)) { Out("def spawn: no object named " + t[0]); return; }
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { Out("def spawn: no player in the room"); return; }
    RValue depth = GetVar(pid, "depth");
    if (!IsNumberKind(depth)) depth = RValue(0.0);
    if (g_Monsters.empty()) g_DropsAtFirstSpawn = g_DropItemCalls.load();
    int made = 0;
    for (int i = 0; i < n; ++i) {
        const double a = 6.283185307179586 * (i + 0.5) / n;
        RValue id;
        try { id = g_Yytk->CallBuiltin("instance_create_depth", { RValue(px + radius * std::cos(a)), RValue(py + radius * std::sin(a)), depth, oi }); }
        catch (...) { Out("def spawn: instance_create_depth threw at " + std::to_string(i)); break; }
        if (!Alive(id)) { Out("def spawn: instance " + std::to_string(i) + " did not survive its creation"); continue; }
        if (rank >= 1) { try { g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("enemyRarity"), RValue(rank) }); } catch (...) {} }
        g_Monsters.push_back({ id, t[0], (int)rank });
        ++made;
    }
    Out("def spawn: " + std::to_string(made) + " x " + t[0] + (rank >= 1 ? " rank " + Fixed(rank) : "") + " at radius " + Fixed(radius)
        + " around " + Fixed(px) + "," + Fixed(py) + " in " + CurrentRoomName() + "; tracking " + std::to_string(g_Monsters.size()));
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
            + " bar " + Stringify(GetVar(m.id, "healthBarMaxHp")) + " affixes " + Stringify(GetVar(m.id, "affixList")).substr(0, 80));
    }
    Out("def list: " + std::to_string(alive) + " alive of " + std::to_string(g_Monsters.size()) + " spawned; drop calls since the first spawn "
        + std::to_string(g_DropItemCalls.load() - g_DropsAtFirstSpawn) + "; tower hits " + std::to_string(g_Tower.hits) + ", kills "
        + std::to_string(g_Tower.kills) + ", failed " + std::to_string(g_Tower.failed) + "; room " + CurrentRoomName()
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
    const bool prot = ProtectedNumber(m.id, t[1].c_str(), h, v);
    Out("def var #" + t[0] + "." + t[1] + " = " + Stringify(GetVar(m.id, t[1].c_str())).substr(0, 300) + (prot ? " (protected: " + Fixed(v, 2) + ")" : ""));
}

// One hit: pct of max health, or a flat amount. The game notices a monster at
// zero health on its own (or does not: that is part of what the lab measures).
static bool Hit(const Spawned& m, double amount, bool pct, bool& killed)
{
    RValue hHp, hMax; double hp = 0, mx = 0;
    if (!ProtectedNumber(m.id, "enemy_hp", hHp, hp)) return false;
    if (!ProtectedNumber(m.id, "max_hp", hMax, mx) || mx <= 0) mx = hp;
    const double damage = pct ? mx * amount / 100.0 : amount;
    const double next = hp - damage > 0 ? hp - damage : 0.0;
    if (!SetProtected(hHp, next)) return false;
    killed = next <= 0;
    return true;
}

static void HitCommand(const std::vector<std::string>& t)
{
    if (t.size() < 2) { Out("def hit: usage -> def hit <index|all> <amount|pct%>"); return; }
    std::string amountText = t[1];
    const bool pct = !amountText.empty() && amountText.back() == '%';
    if (pct) amountText.pop_back();
    char* end = nullptr;
    const double amount = std::strtod(amountText.c_str(), &end);
    if (!end || *end != '\0' || !(amount > 0)) { Out("def hit: the amount must be a positive number or a percentage"); return; }
    int hits = 0, killed = 0, failed = 0;
    for (size_t i = 0; i < g_Monsters.size(); ++i) {
        if (t[0] != "all" && t[0] != std::to_string(i)) continue;
        if (!Alive(g_Monsters[i].id)) continue;
        bool k = false;
        if (Hit(g_Monsters[i], amount, pct, k)) { ++hits; if (k) ++killed; } else ++failed;
    }
    Out("def hit: " + std::to_string(hits) + " hit, " + std::to_string(killed) + " brought to zero, " + std::to_string(failed) + " failed");
}

static void TowerCommand(const std::vector<std::string>& t)
{
    if (!t.empty() && t[0] == "stop") { g_Tower.on = false; Out("def tower: stopped after " + std::to_string(g_Tower.hits) + " hits, " + std::to_string(g_Tower.kills) + " kills"); return; }
    double pct = 0, range = 350, period = 30;
    if (t.empty() || t[0] != "start" || !Number(t, 1, pct) || !(pct > 0)) {
        Out("def tower: usage -> def tower start <pct-of-max-hp> [range=350] [period-frames=30] | def tower stop"); return;
    }
    Number(t, 2, range); Number(t, 3, period);
    RValue pid; double px, py;
    if (!PlayerAt(pid, px, py)) { Out("def tower: no player to anchor the tower on"); return; }
    g_Tower = TowerState{};
    g_Tower.on = true; g_Tower.pct = pct; g_Tower.range = range; g_Tower.period = period < 1 ? 1 : (int)period; g_Tower.ax = px; g_Tower.ay = py;
    Out("def tower: on at " + Fixed(px) + "," + Fixed(py) + ", " + Fixed(pct, 1) + "% of max health every " + std::to_string(g_Tower.period)
        + " frames, range " + Fixed(range));
}

static void TowerTick()
{
    if (!g_Tower.on || ++g_Tower.frame < g_Tower.period) return;
    g_Tower.frame = 0;
    const Spawned* best = nullptr; double bestDistance = 1e18;
    for (const auto& m : g_Monsters) {
        if (!Alive(m.id)) continue;
        const double x = GetVarNumber(m.id, "x", NAN), y = GetVarNumber(m.id, "y", NAN);
        const double d = std::hypot(x - g_Tower.ax, y - g_Tower.ay);
        if (std::isfinite(d) && d <= g_Tower.range && d < bestDistance) { bestDistance = d; best = &m; }
    }
    if (!best) return;
    bool killed = false;
    if (Hit(*best, g_Tower.pct, true, killed)) { ++g_Tower.hits; if (killed) ++g_Tower.kills; }
    else ++g_Tower.failed;
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
    g_Monsters.clear(); g_Tower = TowerState{};
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
        if (verb == "hit") return HitCommand(t);
        if (verb == "tower") return TowerCommand(t);
        if (verb == "perf") return PerfCommand(t);
        if (verb == "clear") return Clear();
    } catch (...) { Out("def " + verb + ": EXCEPTION"); return; }
    Out("def: usage -> def player | spawn | list | var | hit | tower | perf | clear");
}

static void Frame()
{
    PerfTick();
    if (g_Tower.on) { try { TowerTick(); } catch (...) { ++g_Tower.failed; } }
}

} // namespace DefenseLab
