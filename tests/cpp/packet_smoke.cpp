// Baseline / target tests for the packet model (hub AGENTS.md, "Test Before /
// After"): the canonical form and hash must be stable, volatile variables must
// not change identity, and JSON escaping must round-trip control characters.
#include <AfkExpedition/Packet.hpp>
#include <cstdio>
#include <string>

using namespace AfkExpedition;

static int g_Failed = 0;
#define CHECK(cond, msg) do { if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_Failed; } else { std::printf("ok:   %s\n", msg); } } while (0)

static PacketInput Sample()
{
    PacketInput in;
    in.script = "gml_Script_DropItem";
    in.gameBuildId = "exe-1-1";
    in.room = "Act_03_04";
    in.selfObject = "Orc_Warrior_obj";
    in.monsterKey = "e_orcWarrior_2";
    in.rank = 2;
    in.args = { "1.0", "\"x\"", "[0,4,12]" };
    in.selfVars = { {"nameKey", "\"e_orcWarrior_2\""}, {"dList", "[0,4,12]"}, {"x", "100"}, {"y", "200"}, {"aggroTimer", "5"}, {"max_hp", "169508"} };
    in.expRaw = "168169";
    in.expResolved = "2380";
    in.capturedAt = "2026-09-17T00:00:00Z";
    return in;
}

int main()
{
    // SHA-256 known answers (FIPS 180-4 examples)
    CHECK(Sha256::Of("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256('abc')");
    CHECK(Sha256::Of("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "sha256('')");
    CHECK(Sha256::Of("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")
          == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1", "sha256(two-block message)");

    // JSON escaping
    CHECK(JsonEscape("a\"b\\c\n\t\x01") == "a\\\"b\\\\c\\n\\t\\u0001", "JsonEscape control characters");

    // Baseline: identical inputs hash identically
    PacketInput a = Sample(), b = Sample();
    CHECK(HashOf(a) == HashOf(b), "same packet -> same hash");

    // Target: volatile variables (position, timers, anti-cheat handles) do not change identity
    b.selfVars = { {"nameKey", "\"e_orcWarrior_2\""}, {"dList", "[0,4,12]"}, {"x", "900"}, {"y", "1"}, {"aggroTimer", "99"}, {"max_hp", "170000"} };
    CHECK(HashOf(a) == HashOf(b), "volatile vars ignored by hash");

    // Target: a real difference (dList) changes identity
    b.selfVars[1] = { "dList", "[0,4,12,40]" };
    CHECK(HashOf(a) != HashOf(b), "dList change -> different hash");

    // Target: argument order matters
    PacketInput c = Sample(); c.args = { "\"x\"", "1.0", "[0,4,12]" };
    CHECK(HashOf(a) != HashOf(c), "argument order is part of identity");

    // Target: another game build never matches
    PacketInput d = Sample(); d.gameBuildId = "exe-2-2";
    CHECK(HashOf(a) != HashOf(d), "game build id is part of identity");

    // The file body is valid enough JSON to contain the essentials
    std::string js = ToJson(a, HashOf(a));
    CHECK(js.find("\"packet_hash\"") != std::string::npos && js.find("\"self_snapshot\"") != std::string::npos
          && js.find("\"exp_reward\": {\"raw\": 168169, \"resolved\": 2380}") != std::string::npos, "ToJson contains packet fields");

    CHECK(IsVolatileVar("abilityTextTimer") && IsVolatileVar("x") && !IsVolatileVar("dList") && !IsVolatileVar("nameKey"), "IsVolatileVar classification");

    // Target: protected values (behind anti-cheat handles) are part of identity, handles are not
    PacketInput e = Sample(); e.protectedVars = { {"dCommonChance", "100"}, {"experience", "2380"} };
    PacketInput f = Sample(); f.protectedVars = { {"experience", "2380"}, {"dCommonChance", "100"} };
    PacketInput g = Sample(); g.protectedVars = { {"experience", "2380"}, {"dCommonChance", "250"} };
    CHECK(HashOf(e) == HashOf(f), "protected values hash independent of order");
    CHECK(HashOf(e) != HashOf(g), "protected value change -> different hash");
    CHECK(ToJson(e, HashOf(e)).find("\"protected\": {\n    \"dCommonChance\": 100,\n    \"experience\": 2380\n  }") != std::string::npos, "ToJson writes protected block");

    std::printf("%s (%d failures)\n", g_Failed ? "FAILED" : "PASSED", g_Failed);
    return g_Failed ? 1 : 0;
}
