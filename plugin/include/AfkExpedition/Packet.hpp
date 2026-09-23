#pragma once
// Pure C++ (no YYToolkit, no Windows): packet canonicalisation, JSON string
// escaping and SHA-256. Kept dependency-free so tests/cpp/packet_smoke.cpp can
// exercise the exact code the plugin ships, without a game or an Aurie runtime.
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#include <algorithm>

namespace AfkExpedition {

// ---------------------------------------------------------------- JSON escape
inline std::string JsonEscape(std::string_view s)
{
    std::string out; out.reserve(s.size() + 8);
    static const char* hex = "0123456789abcdef";
    for (unsigned char c : s) {
        switch (c) {
        case '"':  out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b";  break;
        case '\f': out += "\\f";  break;
        case '\n': out += "\\n";  break;
        case '\r': out += "\\r";  break;
        case '\t': out += "\\t";  break;
        default:
            if (c < 0x20) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
            else out += static_cast<char>(c);
        }
    }
    return out;
}

// ------------------------------------------------------------------- SHA-256
class Sha256 {
public:
    Sha256() { Reset(); }
    void Reset() {
        m_H = { 0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u };
        m_Len = 0; m_BufLen = 0;
    }
    void Update(const void* data, size_t n) {
        const uint8_t* p = static_cast<const uint8_t*>(data);
        m_Len += n;
        while (n > 0) {
            const size_t room = size_t(64) - m_BufLen;      // no std::min: <windows.h> defines a min macro
            size_t take = n < room ? n : room;
            std::memcpy(m_Buf + m_BufLen, p, take);
            m_BufLen += take; p += take; n -= take;
            if (m_BufLen == 64) { Block(m_Buf); m_BufLen = 0; }
        }
    }
    void Update(std::string_view s) { Update(s.data(), s.size()); }
    std::string HexDigest() {
        uint64_t bits = m_Len * 8;
        uint8_t pad = 0x80; Update(&pad, 1);
        uint8_t zero = 0;
        while (m_BufLen != 56) Update(&zero, 1);
        uint8_t len[8];
        for (int i = 7; i >= 0; --i) { len[i] = uint8_t(bits & 0xff); bits >>= 8; }
        Update(len, 8);
        static const char* hex = "0123456789abcdef";
        std::string out; out.reserve(64);
        for (uint32_t v : m_H) for (int i = 28; i >= 0; i -= 4) out += hex[(v >> i) & 15];
        return out;
    }
    static std::string Of(std::string_view s) { Sha256 h; h.Update(s); return h.HexDigest(); }
private:
    static uint32_t Rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
    void Block(const uint8_t* p) {
        static const uint32_t K[64] = {
            0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
            0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
            0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
            0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
            0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2 };
        uint32_t w[64];
        for (int i = 0; i < 16; ++i)
            w[i] = (uint32_t(p[i*4]) << 24) | (uint32_t(p[i*4+1]) << 16) | (uint32_t(p[i*4+2]) << 8) | uint32_t(p[i*4+3]);
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = Rotr(w[i-15], 7) ^ Rotr(w[i-15], 18) ^ (w[i-15] >> 3);
            uint32_t s1 = Rotr(w[i-2], 17) ^ Rotr(w[i-2], 19) ^ (w[i-2] >> 10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a = m_H[0], b = m_H[1], c = m_H[2], d = m_H[3], e = m_H[4], f = m_H[5], g = m_H[6], h = m_H[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t S1 = Rotr(e, 6) ^ Rotr(e, 11) ^ Rotr(e, 25);
            uint32_t ch = (e & f) ^ (~e & g);
            uint32_t t1 = h + S1 + ch + K[i] + w[i];
            uint32_t S0 = Rotr(a, 2) ^ Rotr(a, 13) ^ Rotr(a, 22);
            uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + mj;
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        m_H[0] += a; m_H[1] += b; m_H[2] += c; m_H[3] += d; m_H[4] += e; m_H[5] += f; m_H[6] += g; m_H[7] += h;
    }
    std::array<uint32_t, 8> m_H{};
    uint64_t m_Len = 0;
    uint8_t m_Buf[64]{};
    size_t m_BufLen = 0;
};

// ------------------------------------------------------- volatile variables
// Instance variables that change every frame or per instance and must not
// take part in packet identity: position, animation, timers, and the
// anti-cheat handles (the numeric value stored in these is a per-instance key
// into a protected store, never the stat itself).
inline bool IsVolatileVar(std::string_view name)
{
    static const char* exact[] = {
        "x", "y", "xprevious", "yprevious", "xstart", "ystart", "id",
        "image_index", "image_alpha", "image_angle", "image_speed", "image_blend",
        "depth", "layer", "direction", "speed", "hspeed", "vspeed", "friction", "gravity",
        "path_position", "path_index", "path_speed", "sprite_index", "mask_index",
        "anchorX", "anchorY", "attacking", "attackSoundPlayed", "MyKiller", "MyKillerObject",
        // anti-cheat protected handles (numeric keys, not values)
        "max_hp", "hp", "enemy_hp", "damage", "experience", "killExperience",
        "currentHpPercentage", "antiSocialMf",
    };
    for (const char* e : exact) if (name == e) return true;
    auto has = [&](std::string_view needle) { return name.find(needle) != std::string_view::npos; };
    return has("timer") || has("Timer") || has("clock") || has("Clock") || has("alarm") || has("Alarm")
        || has("Delay") || has("delay") || has("Cooldown") || has("cooldown");
}

// ------------------------------------------------------------ packet model
struct PacketInput {
    std::string script;                 // e.g. gml_Script_DropItem
    std::vector<std::string> args;      // each already JSON text
    std::vector<std::pair<std::string, std::string>> selfVars;   // name -> JSON text (all vars)
    std::string room;                   // room name at capture time
    std::string gameBuildId;
    std::string selfObject;
    std::string monsterKey;
    int rank = 0;
    std::string expRaw;                 // JSON text of the raw variable
    std::string expResolved;            // JSON number text or "null"
    std::string capturedAt;             // ISO-8601 UTC
    // Anti-cheat protected variables: the snapshot holds only a per-instance
    // handle for these; this is the value behind the handle at capture time.
    std::vector<std::pair<std::string, std::string>> protectedVars;   // name -> JSON number text
};

// The canonical text hashed into packet_hash. Volatile variables are left out
// so the same monster killed twice at different positions yields one packet.
inline std::string Canonical(const PacketInput& in)
{
    std::vector<std::pair<std::string, std::string>> vars;
    vars.reserve(in.selfVars.size());
    for (const auto& kv : in.selfVars) if (!IsVolatileVar(kv.first)) vars.push_back(kv);
    std::sort(vars.begin(), vars.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    std::string s = "afk-packet/1\n";
    s += "build=" + in.gameBuildId + "\n";
    s += "script=" + in.script + "\n";
    s += "room=" + in.room + "\n";
    s += "argc=" + std::to_string(in.args.size()) + "\n";
    for (const auto& a : in.args) { s += "arg:"; s += a; s += "\n"; }
    for (const auto& kv : vars) { s += "self:"; s += kv.first; s += "="; s += kv.second; s += "\n"; }
    std::vector<std::pair<std::string, std::string>> prot = in.protectedVars;
    std::sort(prot.begin(), prot.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    for (const auto& kv : prot) { s += "prot:"; s += kv.first; s += "="; s += kv.second; s += "\n"; }
    return s;
}

inline std::string HashOf(const PacketInput& in) { return Sha256::Of(Canonical(in)); }

// Full packet file body (pretty enough to read, stable key order).
inline std::string ToJson(const PacketInput& in, const std::string& hash)
{
    std::string s = "{\n";
    s += "  \"schema\": 1,\n";
    s += "  \"packet_hash\": \"" + hash + "\",\n";
    s += "  \"game_build_id\": \"" + JsonEscape(in.gameBuildId) + "\",\n";
    s += "  \"script\": \"" + JsonEscape(in.script) + "\",\n";
    s += "  \"captured_at\": \"" + JsonEscape(in.capturedAt) + "\",\n";
    s += "  \"room\": \"" + JsonEscape(in.room) + "\",\n";
    s += "  \"self_object\": \"" + JsonEscape(in.selfObject) + "\",\n";
    s += "  \"monster_key\": \"" + JsonEscape(in.monsterKey) + "\",\n";
    s += "  \"rank\": " + std::to_string(in.rank) + ",\n";
    s += "  \"exp_reward\": {\"raw\": " + (in.expRaw.empty() ? std::string("null") : in.expRaw)
       + ", \"resolved\": " + (in.expResolved.empty() ? std::string("null") : in.expResolved) + "},\n";
    s += "  \"argc\": " + std::to_string(in.args.size()) + ",\n";
    s += "  \"args\": [";
    for (size_t i = 0; i < in.args.size(); ++i) { if (i) s += ", "; s += in.args[i]; }
    s += "],\n";
    s += "  \"protected\": {";
    {
        bool firstP = true;
        for (const auto& kv : in.protectedVars) {
            s += firstP ? "\n" : ",\n"; firstP = false;
            s += "    \"" + JsonEscape(kv.first) + "\": " + kv.second;
        }
    }
    s += "\n  },\n";
    s += "  \"self_snapshot\": {";
    bool first = true;
    for (const auto& kv : in.selfVars) {
        s += first ? "\n" : ",\n"; first = false;
        s += "    \"" + JsonEscape(kv.first) + "\": " + kv.second;
    }
    s += "\n  }\n}\n";
    return s;
}

} // namespace AfkExpedition
