// Research R1 (Stronghold design, 2026-09-26): the in-game window lab.
//
// Questions it answers in a running offline game:
// - Can the plugin draw its own window inside Hero Siege, above the HUD and
//   below the mouse cursor? (drawn from the cursor object's Draw GUI event,
//   just before the cursor itself)
// - Can the player click a button in it without the click reaching the game?
//   (mouse messages over the window are swallowed in the window procedure;
//   the game's own button state is sampled afterwards to prove it, and a
//   press outside the window is the control)
// - Can a hotkey open it, and can a Steward stand in town and offer it?
//   (the Steward is a room layer sprite of an existing NPC's art: a picture
//   only, no game object and no behaviour)
//
// Commands (`afk ui ...`), research build only:
//   ui open | close | status
//   ui selftest                      posts presses and F6 to the game window; results in `ui status`
//   ui hook <event name>             default gml_Object_objColorBlindShader_Draw_75 (drawn after it)
//   ui objects <name part>           object names; `ui find <object>` resolves its GUI draw events
//   ui scan <suffix>                 object events named ..._<suffix> whose object has live instances
//   ui order <event> [event ...]     probe detours record the order those events run in; `ui order` reports
//   ui after on|off                  draw after the hooked event's own code instead of before
//   ui sprites <name part> | ui fonts | ui font <font name>
//   ui npc here [sprite] [dx] | ui npc off     default Guild_Master_NPC_spr, dx 140
//   ui live <name part>              objects with live instances: count, first position, depth, sprite
//   ui tex <sprite>                  the sprite's texture group, its load status, texture_is_ready
//   ui mark <sprite> <dx> <dy> <depth> [prefetch] | ui marks off   layer-sprite markers around the hero
// F6 toggles the window while the lab is enabled (any `ui` command enables it).
//
// Nothing runs until a `ui` command enables the lab. Drawing, input handling
// and commands all run on the game's thread.
//
// MEASURED 2026-09-26: YYToolkit's object-event callback is unavailable on
// this YYC build (CreateCallback EVENT_OBJECT_CALL -> AURIE_UNAVAILABLE), so
// the window draws from an inline detour on the chosen event, resolved by name
// like the script hooks (ResolveYycFunctionByName).
namespace InGameUI {

static bool g_Enabled = false;
static bool g_Open = false;
// MEASURED 2026-09-26 in town: the cursor object has no instance; the GUI
// events run Loot_Manager (GUI Begin), Controller, UI_Inventory_Drag,
// Menu_Controller (GUI), then objColorBlindShader (GUI End) last. The window
// draws right after that last one.
static std::string g_TargetName = "gml_Object_objColorBlindShader_Draw_75";
static CCode* g_TargetCode = nullptr;
static uint64_t g_Draws = 0;
static int g_Clicks = 0, g_Swallowed = 0, g_Toggles = 0, g_MissedClicks = 0;
static double g_GuiW = 0, g_GuiH = 0, g_WinPixW = 0, g_WinPixH = 0;
static double g_MouseGuiX = -1, g_MouseGuiY = -1;             // the game's GUI mouse, at the last draw
static double g_MouseWinX = -1, g_MouseWinY = -1;             // the game's window mouse, at the last draw
static double g_LastClickGuiX = -1, g_LastClickGuiY = -1;     // mapped from the window message
static std::atomic<bool> g_PendingClick{ false }, g_PendingToggle{ false }, g_PendingOutside{ false }, g_PendingKey{ false };
static bool g_DownSwallowed[2] = { false, false };            // left, right: a release follows its press
static HWND g_Hwnd = nullptr;
static double g_Scale = 1, g_TextScale = 1;
static RValue g_Font;
static bool g_HasFont = false;
static std::string g_FontName = "the game's current font";
static std::string g_DrawFont = "?";               // the font the last draw used

struct Rect { double x = 0, y = 0, w = 0, h = 0; bool Has(double px, double py) const { return px >= x && px <= x + w && py >= y && py <= y + h; } };
static Rect g_Win, g_Button, g_Close;

struct NpcState { bool on = false; double x = 0, y = 0; std::string room, sprite; RValue layer, element; bool heroNear = false; };
static NpcState g_Npc;

// After a press the window swallowed, the game's own button state must stay
// up for a few frames. After a press it let through, the state must go down:
// that control proves the sampling sees the input at all.
struct Probe { int frames = 0; bool seen = false; int checks = 0, hits = 0; };
static Probe g_SwallowProbe, g_ControlProbe, g_KeyProbe;

static void Arm(Probe& p, int frames)
{
    if (p.frames > 0) { ++p.checks; if (p.seen) ++p.hits; }
    p.frames = frames; p.seen = false;
}

static void Sample(Probe& p, bool now)
{
    if (p.frames <= 0) return;
    if (now) p.seen = true;
    if (--p.frames == 0) { ++p.checks; if (p.seen) ++p.hits; }
}

static double Num(const char* builtin, std::vector<RValue> args = {})
{
    try { RValue r = g_Yytk->CallBuiltin(builtin, args); return IsNumberKind(r) ? r.ToDouble() : NAN; } catch (...) { return NAN; }
}

static RValue Get(const char* builtin, std::vector<RValue> args = {})
{
    try { return g_Yytk->CallBuiltin(builtin, args); } catch (...) { return RValue(); }
}

static void Call(const char* builtin, std::vector<RValue> args)
{
    try { g_Yytk->CallBuiltin(builtin, args); } catch (...) {}
}

static std::string Lower(std::string s) { for (char& c : s) c = (char)std::tolower((unsigned char)c); return s; }
static std::string Int(double v) { return std::isfinite(v) ? std::to_string((long long)std::llround(v)) : std::string("?"); }

static double Color(int r, int g, int b) { return Num("make_color_rgb", { RValue((double)r), RValue((double)g), RValue((double)b) }); }

static bool GameMouseDown() { return Num("mouse_check_button", { RValue(1.0) }) > 0.5; }        // mb_left
static bool GameKeyDown(int vk) { return Num("keyboard_check", { RValue((double)vk) }) > 0.5; }

static HWND GameWindow()
{
    if (g_Hwnd && IsWindow(g_Hwnd)) return g_Hwnd;
    RValue h = Get("window_handle");
    if (h.m_Kind == VALUE_PTR) g_Hwnd = static_cast<HWND>(h.m_Pointer);
    return g_Hwnd;
}

// Window pixels per GUI unit. A minimized window reports no size; posted
// test input then works in GUI units 1:1.
static double PixW() { return g_WinPixW > 0 ? g_WinPixW : g_GuiW; }
static double PixH() { return g_WinPixH > 0 ? g_WinPixH : g_GuiH; }

static void Box(const Rect& r, double color, double alpha, bool outline)
{
    Call("draw_set_alpha", { RValue(alpha) });
    Call("draw_set_color", { RValue(color) });
    Call("draw_rectangle", { RValue(r.x), RValue(r.y), RValue(r.x + r.w), RValue(r.y + r.h), RValue(outline) });
}

static void Text(double x, double y, const std::string& s, double color, double scale = 1.0)
{
    const double t = g_TextScale * scale;
    Call("draw_set_alpha", { RValue(1.0) });
    Call("draw_set_color", { RValue(color) });
    Call("draw_text_transformed", { RValue(x), RValue(y), RValue(s), RValue(t), RValue(t), RValue(0.0) });
}

static void DrawWindow()
{
    g_GuiW = Num("display_get_gui_width"); g_GuiH = Num("display_get_gui_height");
    g_WinPixW = Num("window_get_width"); g_WinPixH = Num("window_get_height");
    g_MouseGuiX = Num("device_mouse_x_to_gui", { RValue(0.0) }); g_MouseGuiY = Num("device_mouse_y_to_gui", { RValue(0.0) });
    g_MouseWinX = Num("window_mouse_get_x"); g_MouseWinY = Num("window_mouse_get_y");
    if (!std::isfinite(g_GuiW) || g_GuiW <= 0 || !std::isfinite(g_GuiH) || g_GuiH <= 0) return;
    if (!(g_Npc.on && g_Npc.heroNear) && !g_Open) return;

    // The game's draw state is put back afterwards: the cursor, drawn right
    // after this, and every later GUI event expect their own.
    const double alpha0 = Num("draw_get_alpha"), color0 = Num("draw_get_colour");
    const double halign0 = Num("draw_get_halign"), valign0 = Num("draw_get_valign");
    const RValue font0 = Get("draw_get_font");

    // Sizes follow the GUI height (1080 = 1), the text follows the font's own
    // height, so the window reads the same whatever the game set up.
    const double s = std::clamp(g_GuiH / 1080.0, 0.3, 3.0);
    g_Scale = s;
    Call("draw_set_halign", { RValue(0.0) }); Call("draw_set_valign", { RValue(0.0) });
    if (g_HasFont) Call("draw_set_font", { g_Font });
    { const RValue f = Get("draw_get_font"); const RValue n = Num("font_exists", { f }) > 0.5 ? Get("font_get_name", { f }) : RValue();
      g_DrawFont = n.m_Kind == VALUE_STRING ? n.ToString() : std::string("none"); }
    const double mh = Num("string_height", { RValue(std::string("M")) });
    g_TextScale = (std::isfinite(mh) && mh > 0) ? std::clamp(22.0 * s / mh, 0.2, 8.0) : 1.0;

    const double ink = Color(236, 232, 224), muted = Color(170, 176, 190), ember = Color(229, 138, 78), dark = Color(18, 21, 27), slate = Color(60, 66, 80);
    if (g_Npc.on && g_Npc.heroNear && !g_Open) {
        const Rect prompt{ g_GuiW / 2 - 170 * s, g_GuiH - 150 * s, 340 * s, 36 * s };
        Box(prompt, dark, 0.8, false);
        Box(prompt, ember, 1.0, true);
        Text(prompt.x + 14 * s, prompt.y + 7 * s, "[F6] Talk to the Steward", ink);
    }
    if (g_Open) {
        g_Win = Rect{ g_GuiW / 2 - 230 * s, g_GuiH * 0.16, 460 * s, 250 * s };
        g_Button = Rect{ g_Win.x + 20 * s, g_Win.y + g_Win.h - 64 * s, 220 * s, 44 * s };
        g_Close = Rect{ g_Win.x + g_Win.w - 40 * s, g_Win.y + 10 * s, 28 * s, 28 * s };
        Box(g_Win, dark, 0.9, false);
        Box(g_Win, ember, 1.0, true);
        Text(g_Win.x + 20 * s, g_Win.y + 14 * s, "STRONGHOLD", ember, 1.2);
        Text(g_Win.x + 20 * s, g_Win.y + 54 * s, "Research R1: a window the game draws", ink);
        Text(g_Win.x + 20 * s, g_Win.y + 84 * s, "Room: " + CurrentRoomName(), muted);
        Text(g_Win.x + 20 * s, g_Win.y + 114 * s, "Button clicks: " + std::to_string(g_Clicks), muted);
        const bool hover = g_Button.Has(g_MouseGuiX, g_MouseGuiY);
        Box(g_Button, hover ? ember : slate, 1.0, false);
        Text(g_Button.x + 16 * s, g_Button.y + 11 * s, "Collect all (test)", hover ? dark : ink);
        Box(g_Close, g_Close.Has(g_MouseGuiX, g_MouseGuiY) ? ember : slate, 1.0, false);
        Text(g_Close.x + 8 * s, g_Close.y + 3 * s, "X", ink);
    }

    if (std::isfinite(alpha0)) Call("draw_set_alpha", { RValue(alpha0) });
    if (std::isfinite(color0)) Call("draw_set_colour", { RValue(color0) });
    if (std::isfinite(halign0)) Call("draw_set_halign", { RValue(halign0) });
    if (std::isfinite(valign0)) Call("draw_set_valign", { RValue(valign0) });
    if (g_HasFont && font0.m_Kind != VALUE_UNDEFINED && Num("font_exists", { font0 }) > 0.5) Call("draw_set_font", { font0 });
}

using PFN_Event = void(*)(CInstance*, CInstance*);
static PFN_Event g_OrigEvent = nullptr;
static std::string g_HookedName;
static const char* const HOOK_ID = "AfkUiGuiEvent";

// `ui order`: probe detours count where each event runs within a frame.
static constexpr int MAX_PROBES = 8;
struct OrderProbe { std::string name; PFN_Event orig = nullptr; uint64_t calls = 0; int frameCalls = 0, lastPos = 0, pos = 0, perFrame = 0; };
static OrderProbe g_Order[MAX_PROBES];
static int g_OrderCount = 0, g_Seq = 0, g_DrawPos = 0, g_DrawPosShown = 0;
static bool g_DrawAfter = true;

template <int I> static void OrderHook(CInstance* self, CInstance* other)
{
    OrderProbe& p = g_Order[I];
    ++p.calls; ++p.frameCalls; p.lastPos = ++g_Seq;
    if (p.orig) p.orig(self, other);
}
static const PFN_Event g_OrderHooks[MAX_PROBES] = { OrderHook<0>, OrderHook<1>, OrderHook<2>, OrderHook<3>, OrderHook<4>, OrderHook<5>, OrderHook<6>, OrderHook<7> };

// The window draws just before the hooked event's own code (or right after it
// with `ui after on`); whatever runs later in the frame lands on top of it.
static void Hook_GuiEvent(CInstance* self, CInstance* other)
{
    const bool draw = g_Enabled;
    if (draw) { ++g_Draws; if (!g_TargetCode) g_TargetCode = reinterpret_cast<CCode*>(1); }
    if (draw && !g_DrawAfter) { g_DrawPos = ++g_Seq; try { DrawWindow(); } catch (...) {} }
    if (g_OrigEvent) g_OrigEvent(self, other);
    if (draw && g_DrawAfter) { g_DrawPos = ++g_Seq; try { DrawWindow(); } catch (...) {} }
}

static bool HookEvent(const std::string& name)
{
    if (!g_HookedName.empty()) { MmRemoveHook(g_Module, HOOK_ID); g_OrigEvent = nullptr; g_HookedName.clear(); }
    g_TargetName = name; g_TargetCode = nullptr;
    std::string note;
    const auto t0 = std::chrono::steady_clock::now();
    const uintptr_t fn = ResolveYycFunctionByName(name.c_str(), note);
    const long long ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    if (!fn) { Out("ui hook: " + name + " not found (" + note + ", " + std::to_string(ms) + " ms)"); return false; }
    PVOID tramp = nullptr;
    const AurieStatus st = MmCreateHook(g_Module, HOOK_ID, reinterpret_cast<PVOID>(fn), reinterpret_cast<PVOID>(Hook_GuiEvent), &tramp);
    if (!AurieSuccess(st) || !tramp) { Out("ui hook: detour at " + name + " failed st=" + std::to_string((int)st)); return false; }
    g_OrigEvent = reinterpret_cast<PFN_Event>(tramp); g_HookedName = name;
    Out("ui hook: the window draws before " + name + " [" + note + ", resolved in " + std::to_string(ms) + " ms]");
    return true;
}

// EVENT_WNDPROC: the hotkey, and mouse presses over the open window, never
// reach the game. A release is swallowed only when its press was, so the game
// never sees a button stay down.
static void WndProc(FWWndProc& ctx)
{
    if (!g_Enabled) return;
    auto& args = ctx.Arguments();
    if (!g_Hwnd) g_Hwnd = std::get<0>(args);
    const UINT msg = std::get<1>(args);
    const WPARAM wparam = std::get<2>(args);
    const LPARAM lparam = std::get<3>(args);
    if (msg == WM_KEYDOWN && wparam == VK_F6) {
        if (!(lparam & (1 << 30))) { g_PendingToggle = true; g_PendingKey = true; }
        ctx.Override(0); return;
    }
    if (msg == WM_KEYUP && wparam == VK_F6) { ctx.Override(0); return; }
    const int button = (msg == WM_LBUTTONDOWN || msg == WM_LBUTTONUP || msg == WM_LBUTTONDBLCLK) ? 0
        : (msg == WM_RBUTTONDOWN || msg == WM_RBUTTONUP || msg == WM_RBUTTONDBLCLK) ? 1 : -1;
    if (button < 0) return;
    if (msg == WM_LBUTTONUP || msg == WM_RBUTTONUP) {
        if (g_DownSwallowed[button]) { g_DownSwallowed[button] = false; ++g_Swallowed; ctx.Override(0); }
        return;
    }
    if (!g_Open || g_GuiW <= 0) return;
    const double gx = (double)(short)LOWORD(lparam) * g_GuiW / PixW();
    const double gy = (double)(short)HIWORD(lparam) * g_GuiH / PixH();
    if (!g_Win.Has(gx, gy)) { if (msg == WM_LBUTTONDOWN) g_PendingOutside = true; return; }
    if (msg == WM_LBUTTONDOWN) { g_LastClickGuiX = gx; g_LastClickGuiY = gy; g_PendingClick = true; }
    g_DownSwallowed[button] = true;
    ++g_Swallowed;
    ctx.Override(0);
}

static void NpcRemove()
{
    if (g_Npc.on && g_Npc.room == CurrentRoomName()) {
        Call("layer_sprite_destroy", { g_Npc.element });
        Call("layer_destroy", { g_Npc.layer });
    }
    g_Npc = NpcState{};
}

static void NpcHere(const std::string& sprite, double dx)
{
    NpcRemove();
    RValue pid; double px = 0, py = 0;
    if (!DefenseLab::PlayerAt(pid, px, py)) { Out("ui npc: no player in the room " + CurrentRoomName()); return; }
    const RValue spr = AssetIndexCached(sprite);
    if (spr.m_Kind == VALUE_UNDEFINED || (IsNumberKind(spr) && spr.ToDouble() < 0) || !(Num("sprite_exists", { spr }) > 0.5)) {
        Out("ui npc: no sprite named " + sprite + " (list some with: ui sprites npc)"); return;
    }
    // Rooms sort by depth; an object's depth here follows its y, so the
    // Steward's layer takes the depth the hero has at the same height.
    const double depth = GetVarNumber(pid, "depth", 0.0);
    g_Npc.layer = Get("layer_create", { RValue(depth) });
    if (!(Num("layer_exists", { g_Npc.layer }) > 0.5)) { Out("ui npc: layer_create gave no layer"); g_Npc = NpcState{}; return; }
    g_Npc.element = Get("layer_sprite_create", { g_Npc.layer, RValue(px + dx), RValue(py), spr });
    Call("layer_sprite_speed", { g_Npc.element, RValue(1.0) });
    const bool exists = Num("layer_sprite_exists", { g_Npc.layer, g_Npc.element }) > 0.5;
    g_Npc.on = true; g_Npc.x = px + dx; g_Npc.y = py; g_Npc.room = CurrentRoomName(); g_Npc.sprite = sprite;
    Out("ui npc: the Steward (" + sprite + ", " + Int(Num("sprite_get_width", { spr })) + "x" + Int(Num("sprite_get_height", { spr })) + ", "
        + Int(Num("sprite_get_number", { spr })) + " frames) stands at " + Int(g_Npc.x) + "," + Int(g_Npc.y) + " in " + g_Npc.room
        + "; layer depth " + Int(depth) + " (the hero's), element " + (exists ? "exists" : "MISSING"));
}

// `ui mark`: layer-sprite markers around the hero, each on its own layer.
struct Mark { RValue layer, element; std::string room; };
static std::vector<Mark> g_Marks;

static void MarksOff()
{
    const std::string room = CurrentRoomName();
    for (const Mark& m : g_Marks) if (m.room == room) { Call("layer_sprite_destroy", { m.element }); Call("layer_destroy", { m.layer }); }
    g_Marks.clear();
}

static std::string SpriteName(const RValue& spr)
{
    if (!(Num("sprite_exists", { spr }) > 0.5)) return "-";
    const RValue n = Get("sprite_get_name", { spr });
    return n.m_Kind == VALUE_STRING ? n.ToString() : std::string("?");
}

static void TextureInfo(const std::string& sprite)
{
    const RValue spr = AssetIndexCached(sprite);
    if (!(Num("sprite_exists", { spr }) > 0.5)) { Out("ui tex: no sprite named " + sprite); return; }
    const double index = spr.ToDouble();
    const RValue tex = Get("sprite_get_texture", { spr, RValue(0.0) });
    std::string group = "(not found)", status = "?";
    const RValue names = Get("texturegroup_get_names");
    const int groups = names.m_Kind == VALUE_ARRAY ? (int)Num("array_length", { names }) : -1;
    for (int g = 0; g < groups && group == "(not found)"; ++g) {
        const RValue gname = Get("array_get", { names, RValue((double)g) });
        if (gname.m_Kind != VALUE_STRING) continue;
        const RValue sprites = Get("texturegroup_get_sprites", { gname });
        const int n = sprites.m_Kind == VALUE_ARRAY ? (int)Num("array_length", { sprites }) : 0;
        for (int i = 0; i < n; ++i) {
            if (Get("array_get", { sprites, RValue((double)i) }).ToDouble() != index) continue;
            group = gname.ToString();
            status = Int(Num("texturegroup_get_status", { gname }));
            break;
        }
    }
    Out("ui tex " + sprite + " (index " + Int(index) + "): texture_is_ready " + Int(Num("texture_is_ready", { tex })) + ", texture group " + group
        + " status " + status + " (0 unloaded, 1 loading, 2 loaded, 3 fetched), " + std::to_string(groups) + " groups");
}

static void MarkHere(const std::string& sprite, double dx, double dy, double depth, bool prefetch)
{
    RValue pid; double px = 0, py = 0;
    if (!DefenseLab::PlayerAt(pid, px, py)) { Out("ui mark: no player in the room " + CurrentRoomName()); return; }
    const RValue spr = AssetIndexCached(sprite);
    if (!(Num("sprite_exists", { spr }) > 0.5)) { Out("ui mark: no sprite named " + sprite); return; }
    const double pre = prefetch ? Num("sprite_prefetch", { spr }) : NAN;
    Mark m;
    m.layer = Get("layer_create", { RValue(depth) });
    m.element = Get("layer_sprite_create", { m.layer, RValue(px + dx), RValue(py + dy), spr });
    Call("layer_sprite_speed", { m.element, RValue(1.0) });
    m.room = CurrentRoomName();
    const bool ok = Num("layer_sprite_exists", { m.layer, m.element }) > 0.5;
    g_Marks.push_back(m);
    Out("ui mark " + std::to_string(g_Marks.size()) + ": " + sprite + " at hero" + (dx >= 0 ? "+" : "") + Int(dx) + "," + (dy >= 0 ? "+" : "") + Int(dy)
        + " = " + Int(px + dx) + "," + Int(py + dy) + ", depth " + Int(depth) + (prefetch ? ", sprite_prefetch -> " + Int(pre) : "")
        + ", element " + (ok ? "exists" : "MISSING"));
}

// `ui live`: objects with live instances whose name contains the part.
static void LiveObjects(const std::string& part)
{
    const std::string want = Lower(part);
    int misses = 0, shown = 0, total = 0;
    for (int i = 0; i < 20000 && misses < 200; ++i) {
        if (!(Num("object_exists", { RValue((double)i) }) > 0.5)) { ++misses; continue; }
        misses = 0;
        const double n = Num("instance_number", { RValue((double)i) });
        if (!(n > 0)) continue;
        const RValue nm = Get("object_get_name", { RValue((double)i) });
        const std::string name = nm.m_Kind == VALUE_STRING ? nm.ToString() : std::string("?");
        if (!want.empty() && Lower(name).find(want) == std::string::npos) continue;
        ++total;
        if (++shown > 70) continue;
        const RValue id = Get("instance_find", { RValue((double)i), RValue(0.0) });
        Out("  " + name + " x" + Int(n) + ": " + Int(GetVarNumber(id, "x", NAN)) + "," + Int(GetVarNumber(id, "y", NAN)) + " depth " + Int(GetVarNumber(id, "depth", NAN))
            + " sprite " + SpriteName(GetVar(id, "sprite_index")) + " visible " + Int(GetVarNumber(id, "visible", NAN)));
    }
    Out("  " + std::to_string(total) + " objects with live instances match" + (total > 70 ? " (first 70 shown)" : ""));
}

// `ui selftest`: presses and keys posted to the game's own window, one step a
// frame; the window message arrives on the next frame.
static int g_Test = 0, g_TestWait = 0, g_TestClicks0 = 0, g_TestSwallowed0 = 0, g_TestToggles0 = 0;
static uint64_t g_TestDraws0 = 0;
static bool g_TestSawButton = false, g_TestSawControl = false, g_TestSawF6 = false, g_TestSawF13 = false;
static std::vector<std::string> g_TestLog;
static constexpr int KEY_F13 = 124;                     // no such key on most keyboards; nothing in the game uses it

static void TestLog(const std::string& s) { g_TestLog.push_back(s); Out("ui selftest: " + s); }

static void Post(UINT msg, WPARAM wp, double gx, double gy)
{
    const int cx = (int)std::lround(gx * PixW() / g_GuiW), cy = (int)std::lround(gy * PixH() / g_GuiH);
    PostMessageW(GameWindow(), msg, wp, MAKELPARAM(cx, cy));
}

static void PostKey(bool down, int vk)
{
    const UINT scan = MapVirtualKeyW((UINT)vk, MAPVK_VK_TO_VSC);
    LPARAM lp = 1 | ((LPARAM)scan << 16);
    if (!down) lp |= ((LPARAM)1 << 30) | ((LPARAM)1 << 31);
    PostMessageW(GameWindow(), down ? WM_KEYDOWN : WM_KEYUP, (WPARAM)vk, lp);
}

static void TestStep()
{
    if (g_Test == 0) return;
    const double bx = g_Button.x + g_Button.w / 2, by = g_Button.y + g_Button.h / 2;
    const double ox = g_GuiW * 0.06, oy = g_GuiH * 0.5;             // left edge, outside the window
    switch (g_Test) {
    case 1:
        g_Open = true;
        if (g_Draws >= g_TestDraws0 + 2 && g_Win.w > 0) { g_Test = 2; return; }
        if (++g_TestWait > 240) {
            TestLog("FAILED: the window never drew; " + g_TargetName + (g_TargetCode ? " ran" : " never ran") + " (look for another with: ui objects cursor, ui find <object>)");
            g_Test = 0;
        }
        return;
    case 2:
        if (!GameWindow()) { TestLog("FAILED: no game window handle"); g_Test = 0; return; }
        TestLog(std::string("window ") + (IsIconic(GameWindow()) ? "minimized" : "shown") + ", GUI " + Int(g_GuiW) + "x" + Int(g_GuiH)
            + ", window " + Int(g_WinPixW) + "x" + Int(g_WinPixH) + ", button at " + Int(bx) + "," + Int(by));
        g_TestClicks0 = g_Clicks; g_TestSwallowed0 = g_Swallowed;
        Post(WM_LBUTTONDOWN, MK_LBUTTON, bx, by); g_Test = 3; return;
    case 3:
        g_TestSawButton = GameMouseDown();
        Post(WM_LBUTTONUP, 0, bx, by); g_Test = 4; return;
    case 4:
        TestLog(std::string("press on the button: ") + (g_Clicks == g_TestClicks0 + 1 ? "the window counted it" : "the window did NOT count it")
            + ", " + std::to_string(g_Swallowed - g_TestSwallowed0) + " of 2 mouse messages swallowed, the game "
            + (g_TestSawButton ? "SAW the press (leak)" : "did not see the press"));
        g_Test = 13; return;
    case 13:                                                    // the swallow probe must finish before the control press
        if (g_SwallowProbe.frames > 0) return;
        Post(WM_LBUTTONDOWN, MK_LBUTTON, ox, oy); g_Test = 5; return;
    case 5:
        g_TestSawControl = GameMouseDown();
        Post(WM_LBUTTONUP, 0, ox, oy); g_Test = 6; return;
    case 6:
        TestLog(std::string("control press outside the window: the game ")
            + (g_TestSawControl ? "saw it, so posted presses reach the game and the result above counts"
                                : "did NOT see it either, so posted presses never reach the game and only a real click can tell"));
        PostKey(true, KEY_F13); g_Test = 7; return;
    case 7:
        g_TestSawF13 = GameKeyDown(KEY_F13);
        PostKey(false, KEY_F13); g_Test = 8; return;
    case 8:
        g_TestToggles0 = g_Toggles;
        PostKey(true, VK_F6); g_Test = 9; return;
    case 9:
        g_TestSawF6 = GameKeyDown(VK_F6);
        PostKey(false, VK_F6); g_Test = 10; return;
    case 10:
        TestLog(std::string("F6: ") + (g_Toggles == g_TestToggles0 + 1 ? std::string("toggled the window ") + (g_Open ? "open" : "closed") : std::string("did NOT toggle"))
            + ", the game " + (g_TestSawF6 ? "SAW F6 (leak)" : "did not see F6") + "; control key F13: the game " + (g_TestSawF13 ? "saw it" : "did NOT see it"));
        PostKey(true, VK_F6); g_Test = 11; return;
    case 11:
        PostKey(false, VK_F6); g_Test = 12; return;
    default:
        TestLog(std::string("F6 again: window ") + (g_Open ? "open" : "closed") + "; self-test done");
        g_Test = 0; return;
    }
}

static std::string ProbeText(const Probe& p) { return std::to_string(p.hits) + " of " + std::to_string(p.checks); }

static void Status()
{
    Out("ui status: enabled " + std::string(g_Enabled ? "yes" : "no") + ", window " + (g_Open ? "open" : "closed") + ", event " + g_TargetName
        + (g_TargetCode ? " (ran)" : " (not run yet)") + ", draws " + std::to_string(g_Draws));
    const HWND hwnd = GameWindow();
    Out("  game window " + std::string(!hwnd ? "unknown" : IsIconic(hwnd) ? "minimized" : "shown") + ", GUI " + Int(g_GuiW) + "x" + Int(g_GuiH)
        + ", window " + Int(g_WinPixW) + "x" + Int(g_WinPixH) + ", layout scale " + std::to_string(g_Scale).substr(0, 4)
        + ", text scale " + std::to_string(g_TextScale).substr(0, 4) + " (" + g_FontName + ", drew with " + g_DrawFont + ")");
    Out("  mouse at the last draw: window " + Int(g_MouseWinX) + "," + Int(g_MouseWinY) + " -> GUI by window size " + Int(g_MouseWinX * g_GuiW / PixW()) + ","
        + Int(g_MouseWinY * g_GuiH / PixH()) + ", GUI by the game " + Int(g_MouseGuiX) + "," + Int(g_MouseGuiY));
    Out("  button clicks " + std::to_string(g_Clicks) + ", other presses in the window " + std::to_string(g_MissedClicks) + ", mouse messages swallowed "
        + std::to_string(g_Swallowed) + ", F6 toggles " + std::to_string(g_Toggles) + ", last swallowed press at GUI " + Int(g_LastClickGuiX) + "," + Int(g_LastClickGuiY));
    Out("  the game's own input state: swallowed presses it saw " + ProbeText(g_SwallowProbe) + ", presses outside it saw " + ProbeText(g_ControlProbe)
        + " (control), swallowed F6 it saw " + ProbeText(g_KeyProbe));
    Out("  Steward " + std::string(g_Npc.on ? "at " + Int(g_Npc.x) + "," + Int(g_Npc.y) + " in " + g_Npc.room + (g_Npc.heroNear ? " (the hero is near)" : " (the hero is not near)") : "not placed"));
    if (g_Test) Out("  self-test running (step " + std::to_string(g_Test) + ")");
    Out("  hooked event: " + (g_HookedName.empty() ? std::string("none") : g_HookedName));
    for (const std::string& line : g_TestLog) Out("  selftest: " + line);
}

static void OrderStart(std::istream& names)
{
    for (int i = 0; i < g_OrderCount; ++i) MmRemoveHook(g_Module, "AfkUiOrder" + std::to_string(i));
    for (auto& p : g_Order) p = OrderProbe{};
    g_OrderCount = 0;
    std::string name;
    while (g_OrderCount < MAX_PROBES && names >> name) {
        if (name == g_HookedName) { Out("  " + name + ": skipped, the window's own detour sits there (its position is reported as 'the window')"); continue; }
        std::string note;
        const uintptr_t fn = ResolveYycFunctionByName(name.c_str(), note);
        if (!fn) { Out("  " + name + ": not found (" + note + ")"); continue; }
        OrderProbe& p = g_Order[g_OrderCount];
        PVOID tramp = nullptr;
        const AurieStatus st = MmCreateHook(g_Module, "AfkUiOrder" + std::to_string(g_OrderCount), reinterpret_cast<PVOID>(fn), reinterpret_cast<PVOID>(g_OrderHooks[g_OrderCount]), &tramp);
        if (!AurieSuccess(st) || !tramp) { Out("  " + name + ": detour failed st=" + std::to_string((int)st)); continue; }
        p.name = name; p.orig = reinterpret_cast<PFN_Event>(tramp);
        Out("  probing " + name);
        ++g_OrderCount;
    }
}

static void OrderReport()
{
    std::vector<std::pair<int, std::string>> rows;
    for (int i = 0; i < g_OrderCount; ++i) {
        const OrderProbe& p = g_Order[i];
        rows.push_back({ p.pos ? p.pos : 9999, p.name + ": " + std::to_string(p.calls) + " calls, " + std::to_string(p.perFrame) + " in the last frame" });
    }
    if (g_Enabled && !g_HookedName.empty()) rows.push_back({ g_DrawPosShown ? g_DrawPosShown : 9999, "the window (" + std::string(g_DrawAfter ? "after " : "before ") + g_HookedName + ")" });
    std::sort(rows.begin(), rows.end());
    Out("ui order (last frame, first to last; later draws on top):");
    for (const auto& row : rows) Out("  " + (row.first == 9999 ? std::string("-") : std::to_string(row.first)) + ". " + row.second);
}

// `ui scan`: every compiled object event is named gml_Object_<object>_<event>;
// the names sit in the image's read-only data.
static void ScanEvents(const std::string& suffix)
{
    static const char prefix[] = "gml_Object_";
    const size_t plen = sizeof(prefix) - 1;
    std::set<std::string> names;
    for (const auto& sec : ModuleSections(GetModuleHandleA(nullptr))) {
        if (!sec.read || sec.exec) continue;
        const uint8_t* at = sec.begin;
        while (at < sec.end) {
            const uint8_t* hit = static_cast<const uint8_t*>(std::memchr(at, 'g', (size_t)(sec.end - at)));
            if (!hit) break;
            at = hit + 1;
            if ((size_t)(sec.end - hit) < plen + 2 || std::memcmp(hit, prefix, plen) != 0 || (hit > sec.begin && hit[-1] != 0)) continue;
            const size_t room = (size_t)(sec.end - hit) < 200 ? (size_t)(sec.end - hit) : 200;
            const uint8_t* z = static_cast<const uint8_t*>(std::memchr(hit, 0, room));
            if (!z) continue;
            std::string name(reinterpret_cast<const char*>(hit), (size_t)(z - hit));
            if (name.size() > suffix.size() + plen && name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0) names.insert(name);
            at = z;
        }
    }
    int live = 0;
    for (const std::string& name : names) {
        const std::string object = name.substr(plen, name.size() - plen - suffix.size() - 1);
        RValue obj;
        if (!ObjectIndex(object, obj)) continue;
        const double n = Num("instance_number", { obj });
        if (!(n > 0)) continue;
        ++live;
        Out("  " + name + " (" + Int(n) + " live)");
    }
    Out("  " + std::to_string(names.size()) + " events named ..._" + suffix + ", " + std::to_string(live) + " with live instances");
}

// Which GUI draw events an object has: Draw GUI Begin (74), Draw GUI (64) and
// Draw GUI End (75), resolved by name in the game's code.
static void FindEvents(const std::string& object)
{
    for (const char* ev : { "Draw_74", "Draw_64", "Draw_75" }) {
        const std::string name = "gml_Object_" + object + "_" + ev;
        std::string note;
        const uintptr_t fn = ResolveYycFunctionByName(name.c_str(), note);
        Out("  " + name + ": " + (fn ? std::string("exists") : "no (" + note + ")"));
    }
}

static void ListAssets(const char* exists, const char* nameOf, const std::string& part, int limit)
{
    const std::string want = Lower(part);
    int misses = 0, found = 0, total = 0;
    std::string line;
    for (int i = 0; i < 40000 && misses < 400; ++i) {
        if (!(Num(exists, { RValue((double)i) }) > 0.5)) { ++misses; continue; }
        misses = 0; ++total;
        RValue n = Get(nameOf, { RValue((double)i) });
        const std::string name = n.m_Kind == VALUE_STRING ? n.ToString() : std::string();
        if (!want.empty() && Lower(name).find(want) == std::string::npos) continue;
        if (++found > limit) continue;
        line += (line.empty() ? "" : ", ") + name;
        if (line.size() > 160) { Out("  " + line); line.clear(); }
    }
    if (!line.empty()) Out("  " + line);
    Out("  " + std::to_string(found) + " of " + std::to_string(total) + " match" + (found > limit ? " (first " + std::to_string(limit) + " shown)" : ""));
}

// The window-message callback and the draw detour exist only after the first
// `ui` command.
static bool g_Registered = false;
static void Register()
{
    if (g_Registered) return;
    g_Registered = true;
    const AurieStatus b = g_Yytk->CreateCallback(g_Module, EVENT_WNDPROC, (PVOID)WndProc, 0);
    Out("ui: window-message callback st=" + std::to_string((int)b));
    HookEvent(g_TargetName);
}

static void Command(const std::string& verb, const std::string& first, std::istream& rest)
{
    std::string second; rest >> second;
    g_Enabled = true;
    try {
        if (verb == "open") { g_Open = true; Out("ui: window open (F6 toggles it)"); return; }
        if (verb == "close") { g_Open = false; Out("ui: window closed"); return; }
        if (verb == "status") { Status(); return; }
        if (verb == "selftest") {
            g_TestLog.clear(); g_Test = 1; g_TestWait = 0; g_TestDraws0 = g_Draws;
            g_SwallowProbe = Probe{}; g_ControlProbe = Probe{}; g_KeyProbe = Probe{};
            Out("ui selftest: started; it takes about 15 frames, then read `ui status`"); return;
        }
        if (verb == "hook") { if (first.empty()) Out("ui hook: " + g_TargetName); else HookEvent(first); return; }
        if (verb == "objects") { Out("ui objects '" + first + "':"); ListAssets("object_exists", "object_get_name", first, 120); return; }
        if (verb == "find") { Out("ui find " + first + ":"); FindEvents(first); return; }
        if (verb == "scan") { Out("ui scan " + first + ":"); ScanEvents(first.empty() ? std::string("Draw_64") : first); return; }
        if (verb == "order") {
            if (first.empty()) { OrderReport(); return; }
            std::string list = first + ' ' + second, more; while (rest >> more) list += ' ' + more; std::istringstream names(list);
            Out("ui order:"); OrderStart(names); return;
        }
        if (verb == "after") { g_DrawAfter = first == "on"; Out(std::string("ui: the window draws ") + (g_DrawAfter ? "after" : "before") + " " + g_HookedName); return; }
        if (verb == "sprites") { Out("ui sprites '" + first + "':"); ListAssets("sprite_exists", "sprite_get_name", first, 120); return; }
        if (verb == "fonts") { Out("ui fonts:"); ListAssets("font_exists", "font_get_name", first, 120); return; }
        if (verb == "font") {
            const RValue f = AssetIndexCached(first);
            if (first.empty() || f.m_Kind == VALUE_UNDEFINED || (IsNumberKind(f) && f.ToDouble() < 0) || !(Num("font_exists", { f }) > 0.5)) {
                Out("ui font: no font named '" + first + "' (list them with: ui fonts)"); return;
            }
            g_Font = f; g_HasFont = true; g_FontName = first; Out("ui font: " + first); return;
        }
        if (verb == "live") { Out("ui live '" + first + "':"); LiveObjects(first); return; }
        if (verb == "tex") { TextureInfo(first); return; }
        if (verb == "marks") { MarksOff(); Out("ui marks: removed"); return; }
        if (verb == "mark") {
            std::string sdy, sdepth, sopt; rest >> sdy >> sdepth >> sopt;
            double dx = 0, dy = 0, depth = -411;
            try { dx = std::stod(second); dy = std::stod(sdy); depth = std::stod(sdepth); } catch (...) { Out("ui mark: usage -> ui mark <sprite> <dx> <dy> <depth> [prefetch]"); return; }
            MarkHere(first, dx, dy, depth, sopt == "prefetch"); return;
        }
        if (verb == "npc") {
            if (first == "off") { NpcRemove(); Out("ui npc: removed"); return; }
            if (first == "here") {
                std::string third; rest >> third;
                double dx = 140;
                try { if (!third.empty()) dx = std::stod(third); } catch (...) {}
                NpcHere(second.empty() ? std::string("Guild_Master_NPC_spr") : second, dx); return;
            }
        }
    } catch (...) { Out("ui " + verb + ": EXCEPTION"); return; }
    Out("ui: usage -> ui open | close | status | selftest | hook <event> | objects <part> | find <object> | scan <suffix> | order [events] | after on|off | sprites <part> | fonts | font <name> | npc here [sprite] [dx] | npc off");
}

// Once a frame on the game thread (at Present): F6, a press the window
// swallowed, the input probes, the self-test and whether the hero stands near
// the Steward.
static void Frame()
{
    if (!g_Enabled) return;
    for (int i = 0; i < g_OrderCount; ++i) {
        OrderProbe& p = g_Order[i];
        p.perFrame = p.frameCalls; p.pos = p.frameCalls ? p.lastPos : 0; p.frameCalls = 0;
    }
    g_DrawPosShown = g_DrawPos; g_DrawPos = 0; g_Seq = 0;
    if (g_PendingToggle.exchange(false)) { g_Open = !g_Open; ++g_Toggles; Out(std::string("ui: F6 -> window ") + (g_Open ? "open" : "closed")); }
    if (g_PendingKey.exchange(false)) Arm(g_KeyProbe, 3);
    if (g_PendingClick.exchange(false)) {
        // The same mapped position the window procedure used to swallow it.
        if (g_Close.Has(g_LastClickGuiX, g_LastClickGuiY)) { g_Open = false; Out("ui: closed with X"); }
        else if (g_Button.Has(g_LastClickGuiX, g_LastClickGuiY)) { ++g_Clicks; Out("ui: button clicked (" + std::to_string(g_Clicks) + ")"); }
        else ++g_MissedClicks;
        Arm(g_SwallowProbe, 3);
    }
    if (g_PendingOutside.exchange(false)) Arm(g_ControlProbe, 3);
    if (g_SwallowProbe.frames > 0 || g_ControlProbe.frames > 0) {
        const bool down = GameMouseDown();
        Sample(g_SwallowProbe, down); Sample(g_ControlProbe, down);
    }
    if (g_KeyProbe.frames > 0) Sample(g_KeyProbe, GameKeyDown(VK_F6));
    TestStep();
    if (g_Npc.on) {
        if (g_Npc.room != CurrentRoomName()) { g_Npc.heroNear = false; return; }
        RValue pid; double px = 0, py = 0;
        g_Npc.heroNear = DefenseLab::PlayerAt(pid, px, py) && std::hypot(px - g_Npc.x, py - g_Npc.y) < 120;
    }
}

} // namespace InGameUI
