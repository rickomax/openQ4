#!/usr/bin/env python3
"""Guards the engine and game-module contract for co-operative play.

Co-op is the one networked gametype served by game_sp rather than game_mp: it
runs campaign maps, so it needs the AI, scripting and content that only the
single-player module implements. That makes it the exact case every
"anything but singleplayer means game_mp" rule in the engine gets wrong, and
those rules are spread across module selection, spawnServer, connect and
reconnect. This test pins each of them, and pins the game-side half that keeps
idMultiplayerGame out of a co-op session and replicates campaign AI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GAME_LIBS_ROOT = Path(os.environ.get("OPENQ4_GAMELIBS_REPO", ROOT.parent / "openQ4-game")).resolve()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(haystack: str, needle: str, context: str) -> None:
    if needle not in haystack:
        raise AssertionError(f"Missing {needle!r} in {context}")


def function_body(source: str, signature: str, context: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"Missing {signature!r} in {context}")
    opening = source.find("{", start + len(signature))
    if opening < 0:
        raise AssertionError(f"Missing body for {signature!r} in {context}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unbalanced body for {signature!r} in {context}")


def validate_shared_gametype_header() -> None:
    header = read(ROOT / "src" / "framework" / "CoopGameType.h")
    for token in (
        "namespace idCoopGameType",
        'GAMETYPE_NAME = "Coop"',
        'GAME_MODULE_NAME = "game_sp"',
        "inline bool IsCoopGameTypeName( const char *gameType )",
        "inline bool IsCoopGameTypeActive( void )",
    ):
        require(header, token, "shared co-op gametype header")


def validate_module_selection() -> None:
    common = read(ROOT / "src" / "framework" / "Common.cpp")
    require(common, '#include "CoopGameType.h"', "Common.cpp co-op header include")

    body = function_body(
        common,
        "static const char *openQ4_SelectGameModuleBaseName( void )",
        "game module selection",
    )
    require(body, "idCoopGameType::IsCoopGameTypeName( gameType )", "co-op module selection")
    require(body, "return idCoopGameType::GAME_MODULE_NAME;", "co-op module selection")

    # A dedicated co-op server is the case the historical dedicated reading gets
    # wrong, so the co-op branch has to be reached before it.
    coop_index = body.index("idCoopGameType::IsCoopGameTypeName( gameType )")
    dedicated_index = body.index("#ifdef ID_DEDICATED")
    if coop_index > dedicated_index:
        raise AssertionError(
            "co-op module selection is decided after the dedicated-server branch, "
            "so a dedicated co-op server would load game_mp"
        )


def validate_network_command_routing() -> None:
    async_network = read(ROOT / "src" / "framework" / "async" / "AsyncNetwork.cpp")
    require(async_network, '#include "../CoopGameType.h"', "AsyncNetwork co-op header include")

    helper = function_body(
        async_network,
        "static const char *Net_RequiredGameModule( void )",
        "networked game module requirement",
    )
    require(helper, "idCoopGameType::IsCoopGameTypeActive()", "co-op network module requirement")
    require(helper, "idCoopGameType::GAME_MODULE_NAME", "co-op network module requirement")

    # Each of these forced game_mp unconditionally, which unloaded the module
    # that owns the campaign the co-op session is playing.
    for signature, context in (
        ("void idAsyncNetwork::SpawnServer_f( const idCmdArgs &args )", "spawnServer"),
        ("void idAsyncNetwork::Connect_f( const idCmdArgs &args )", "connect"),
        ("void idAsyncNetwork::Reconnect_f( const idCmdArgs &args )", "reconnect"),
    ):
        body = function_body(async_network, signature, context)
        require(body, "const char *requiredModule = Net_RequiredGameModule();", context)
        require(body, 'cvarSystem->SetCVarString( "com_nextGameModule", requiredModule );', context)
        if 'SetCVarString( "com_nextGameModule", "game_mp" )' in body:
            raise AssertionError(f"{context} still forces game_mp unconditionally")


def validate_loading_screen() -> None:
    session = read(ROOT / "src" / "framework" / "Session.cpp")
    require(session, '#include "CoopGameType.h"', "Session.cpp co-op header include")

    body = function_body(
        session,
        "void idSessionLocal::LoadLoadingGui( const char *mapName )",
        "loading gui selection",
    )
    require(body, "idCoopGameType::IsCoopGameTypeName( spawnGameType )", "co-op loadscreen")
    require(body, "isMultiplayerLoad && !isCoopLoad", "co-op loadscreen")


def validate_game_module_gametype() -> None:
    multiplayer_game = read(GAME_LIBS_ROOT / "src" / "game" / "MultiplayerGame.h")
    require(multiplayer_game, "GAME_COOP,", "single-player module gametype enum")
    # GAME_COOP has to be appended, not inserted, or every existing gametype
    # ordinal on the wire and in saved state shifts underneath it.
    if multiplayer_game.index("GAME_COOP,") > multiplayer_game.index("NUM_GAME_TYPES,"):
        raise AssertionError("GAME_COOP must precede NUM_GAME_TYPES")
    if multiplayer_game.index("GAME_DEADZONE,") > multiplayer_game.index("GAME_COOP,"):
        raise AssertionError("GAME_COOP must be appended after the existing gametypes")

    game_local_h = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.h")
    for token in (
        "bool					IsCoop( void ) const { return gameType == GAME_COOP; }",
        "IsMatchGameType( void ) const { return isMultiplayer && !IsCoop(); }",
        "bool					FindCoopSpawnPosition( idPlayer* player, idVec3 &origin );",
    ):
        require(game_local_h, token, "single-player module co-op predicates")

    game_local = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.cpp")
    set_game_type = function_body(game_local, "void idGameLocal::SetGameType( void )", "gametype selection")
    require(set_game_type, 'if ( !gameTypeName.Icmp( "Coop" ) ) {', "co-op gametype selection")
    require(set_game_type, "gameType = GAME_COOP;", "co-op gametype selection")
    # idMultiplayerGame::SetGameType has no row for co-op and would both error
    # out and overwrite si_entityFilter with the gametype name.
    if set_game_type.index("gameType = GAME_COOP;") > set_game_type.index("mpGame.SetGameType();"):
        raise AssertionError("co-op reaches idMultiplayerGame::SetGameType")

    # The match rules must not run for a co-op session.
    for signature, context in (
        ("gameReturn_t idGameLocal::RunFrame( const usercmd_t *clientCmds, int activeEditors", "game frame"),
        ("bool idGameLocal::Draw( int clientNum )", "game draw"),
    ):
        body = function_body(game_local, signature, context)
        require(body, "IsMatchGameType()", f"co-op exclusion from {context}")


def validate_campaign_spawns() -> None:
    game_local = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.cpp")

    spawns = function_body(game_local, "void idGameLocal::InitializeSpawns( void )", "spawn collection")
    require(spawns, "if ( IsCoop() ) {", "co-op campaign spawn collection")
    require(spawns, '"info_player_start"', "co-op campaign spawn collection")

    select = function_body(game_local, "idEntity* idGameLocal::SelectSpawnPoint( idPlayer* player )", "spawn selection")
    require(select, "if( IsCoop() ) {", "co-op spawn selection")

    # Campaign maps have a single start, so players have to be spread around it.
    find = function_body(
        game_local,
        "bool idGameLocal::FindCoopSpawnPosition( idPlayer* player, idVec3 &origin )",
        "co-op spawn spreading",
    )
    require(find, "MASK_PLAYERSOLID", "co-op spawn spreading")
    require(find, "reach.fraction < 1.0f", "co-op spawn spreading")

    player = read(GAME_LIBS_ROOT / "src" / "game" / "Player.cpp")
    select_player = function_body(
        player,
        "bool idPlayer::SelectSpawnPoint( idVec3 &origin, idAngles &angles )",
        "player spawn selection",
    )
    require(select_player, "gameLocal.FindCoopSpawnPosition( this, origin );", "co-op player spawn spreading")


def validate_ai_replication() -> None:
    ai_header = read(GAME_LIBS_ROOT / "src" / "game" / "ai" / "AI.h")
    for token in (
        "virtual void			WriteToSnapshot					( idBitMsgDelta &msg ) const;",
        "virtual void			ReadFromSnapshot				( const idBitMsgDelta &msg );",
    ):
        require(ai_header, token, "campaign AI replication")

    ai = read(GAME_LIBS_ROOT / "src" / "game" / "ai" / "AI.cpp")
    spawn = function_body(ai, "void idAI::Spawn( void )", "AI spawn")
    require(spawn, "if ( gameLocal.IsCoop() ) {", "campaign AI replication opt-in")
    require(spawn, "fl.networkSync = true;", "campaign AI replication opt-in")

    write = function_body(ai, "void idAI::WriteToSnapshot( idBitMsgDelta &msg ) const", "AI snapshot write")
    read_body = function_body(ai, "void idAI::ReadFromSnapshot( const idBitMsgDelta &msg )", "AI snapshot read")

    # Every field has to be written and read in the same order or the delta
    # message desynchronises for every entity after it in the snapshot.
    for token in ("physicsObj.", "BindToSnapshot", "ActorAnimFromSnapshot", "viewAxis", "health"):
        require(write, token.replace("FromSnapshot", "ToSnapshot"), "AI snapshot write")
        require(read_body, token.replace("ToSnapshot", "FromSnapshot"), "AI snapshot read")

    actor = read(GAME_LIBS_ROOT / "src" / "game" / "Actor.cpp")
    anim_write = function_body(
        actor,
        "void idActor::WriteActorAnimToSnapshot( idBitMsgDelta &msg ) const",
        "actor animation write",
    )
    anim_read = function_body(
        actor,
        "void idActor::ReadActorAnimFromSnapshot( const idBitMsgDelta &msg )",
        "actor animation read",
    )
    require(anim_write, "ACTOR_ANIM_NUM_BITS", "actor animation write")
    require(anim_read, "ACTOR_ANIM_NUM_BITS", "actor animation read")
    # A client whose model def is missing the animation must not index past its
    # own animator.
    require(anim_read, "animNum >= animator.NumAnims()", "actor animation index guard")


def validate_campaign_script_replication() -> None:
    game_local_h = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.h")

    # Appended last, so every existing reliable message keeps its ordinal.
    require(game_local_h, "GAME_RELIABLE_MESSAGE_COOP_CAMPAIGN_EVENT", "co-op campaign reliable message")
    if game_local_h.index("GAME_RELIABLE_MESSAGE_COOP_CAMPAIGN_EVENT") < game_local_h.index(
        "GAME_RELIABLE_MESSAGE_CHEAT_NOCLIP"
    ):
        raise AssertionError("GAME_RELIABLE_MESSAGE_COOP_CAMPAIGN_EVENT must be appended after the existing messages")

    for token in (
        "COOP_CAMPAIGN_EVENT_OBJECTIVE",
        "COOP_CAMPAIGN_EVENT_SECRET_AREA",
        "COOP_CAMPAIGN_EVENT_TIP",
        "COOP_CAMPAIGN_EVENT_TIP_OFF",
        "COOP_CAMPAIGN_EVENT_FADE",
        "COOP_CAMPAIGN_PAYLOAD_STRINGS",
        "COOP_CAMPAIGN_PAYLOAD_FADE",
    ):
        require(game_local_h, token, "co-op campaign event vocabulary")

    game_local = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.cpp")
    for signature in (
        "int idGameLocal::GetCampaignPlayers( idPlayer *players[ MAX_CLIENTS ] ) const",
        "idPlayer *idGameLocal::GetCampaignActivator( idEntity *activator ) const",
        "void idGameLocal::SendCoopCampaignEvent( int eventType, const char *arg0, const char *arg1 )",
        "void idGameLocal::SendCoopCampaignFade( const idVec4 &fadeColor, int fadeTime )",
        "void idGameLocal::QueueCoopMapScript( const function_t *func )",
        "void idGameLocal::StartPendingCoopMapScripts( void )",
    ):
        function_body(game_local, signature, "co-op campaign helpers")

    # Outside co-op GetCampaignPlayers has to resolve to exactly the local
    # player, or every converted call site changes single-player behaviour.
    campaign_players = function_body(
        game_local,
        "int idGameLocal::GetCampaignPlayers( idPlayer *players[ MAX_CLIENTS ] ) const",
        "campaign player set",
    )
    require(campaign_players, "if ( !IsCoop() ) {", "single-player campaign player set")
    require(campaign_players, "idPlayer *local = GetLocalPlayer();", "single-player campaign player set")

    # A listen-server host receives none of its own reliable messages, so the
    # send path has to apply the effect locally too - and exactly once.
    for signature, applier in (
        ("void idGameLocal::SendCoopCampaignEvent( int eventType, const char *arg0, const char *arg1 )",
         "ApplyCoopCampaignEvent( eventType, arg0, arg1 );"),
        ("void idGameLocal::SendCoopCampaignFade( const idVec4 &fadeColor, int fadeTime )",
         "ApplyCoopCampaignFade( fadeColor, fadeTime );"),
    ):
        body = function_body(game_local, signature, "co-op campaign send path")
        require(body, "if ( isClient ) {", "co-op campaign send path")
        require(body, 'networkSystem->ServerSendReliableMessage( -1, outMsg );', "co-op campaign send path")
        if body.count(applier) != 1:
            raise AssertionError(f"{applier!r} must be applied exactly once in the send path")

    # Write order and read order must agree field for field.
    send_fade = function_body(
        game_local,
        "void idGameLocal::SendCoopCampaignFade( const idVec4 &fadeColor, int fadeTime )",
        "co-op campaign fade write",
    )
    network = read(GAME_LIBS_ROOT / "src" / "game" / "Game_network.cpp")
    client_read = function_body(
        network,
        "void idGameLocal::ClientProcessReliableMessage( int clientNum, const idBitMsg &msg )",
        "co-op campaign event read",
    )
    require(client_read, "case GAME_RELIABLE_MESSAGE_COOP_CAMPAIGN_EVENT:", "co-op campaign event read")
    # Payload shape is read before the event type is judged, so an event type a
    # newer server adds is ignored rather than misread.
    if client_read.index("const int payloadKind = msg.ReadByte();") > client_read.index(
        "case COOP_CAMPAIGN_PAYLOAD_STRINGS:"
    ):
        raise AssertionError("co-op campaign payload shape must be read before the payload")
    # Scope the count to the fade case: other payload shapes read floats too.
    fade_case_start = client_read.index("case COOP_CAMPAIGN_PAYLOAD_FADE:")
    fade_case = client_read[fade_case_start : client_read.index("case ", fade_case_start + 1)]
    if send_fade.count("outMsg.WriteFloat(") != 4 or fade_case.count("msg.ReadFloat();") != 4:
        raise AssertionError("co-op campaign fade colour must be four floats on both sides")
    require(send_fade, "outMsg.WriteLong( fadeTime );", "co-op campaign fade write")
    require(client_read, "const int fadeTime = msg.ReadLong();", "co-op campaign fade read")

    # Campaign map scripts must not start before a player exists to run them
    # against - on a dedicated co-op server nobody has even connected.
    worldspawn = function_body(
        read(GAME_LIBS_ROOT / "src" / "game" / "WorldSpawn.cpp"),
        "void idWorldspawn::Spawn( void )",
        "map script startup",
    )
    if worldspawn.count("gameLocal.QueueCoopMapScript( func );") != 2:
        raise AssertionError("both worldspawn script entry points must defer in co-op")

    spawn_player = function_body(
        game_local,
        "void idGameLocal::SpawnPlayer(int clientNum, bool isBot, const char* botName)",
        "player spawn",
    )
    require(spawn_player, "StartPendingCoopMapScripts();", "held map script release")

    # Script-driven screen fades reach every player, not just the host.
    script_thread = read(GAME_LIBS_ROOT / "src" / "game" / "script" / "Script_Thread.cpp")
    for name in ("FadeIn", "FadeOut", "FadeTo"):
        body = function_body(script_thread, f"void idThread::Event_{name}(", f"script {name}")
        require(body, "gameLocal.SendCoopCampaignFade(", f"script {name}")
        if "GetLocalPlayer()" in body:
            raise AssertionError(f"idThread::Event_{name} still fades only the local player")

    # These dereferenced GetLocalPlayer() unchecked, which is NULL on a
    # dedicated co-op server.
    for path, signature, context in (
        (("src", "game", "script", "Script_Thread.cpp"),
         "void idThread::Event_DrawText(", "script debug text"),
        (("src", "game", "Target.cpp"),
         "void rvTarget_AmmoStash::Event_Activate( idEntity *activator )", "ammo stash"),
    ):
        body = function_body(read(GAME_LIBS_ROOT.joinpath(*path)), signature, context)
        if "GetLocalPlayer()->" in body:
            raise AssertionError(f"{context} still dereferences GetLocalPlayer() unchecked")

    # Campaign progression reaches the whole party.
    target = read(GAME_LIBS_ROOT / "src" / "game" / "Target.cpp")
    give = function_body(target, "void idTarget_Give::Event_Activate( idEntity *activator )", "campaign give")
    require(give, "gameLocal.GetCampaignPlayers( players )", "campaign give")
    for signature, event in (
        ("void idTarget_SetPrimaryObjective::Event_Activate( idEntity *activator )", "COOP_CAMPAIGN_EVENT_OBJECTIVE"),
        ("void rvTarget_SecretArea::Event_Activate( idEntity *activator )", "COOP_CAMPAIGN_EVENT_SECRET_AREA"),
        ("void idTarget_Tip::Event_Activate( idEntity *activator )", "COOP_CAMPAIGN_EVENT_TIP"),
        ("void idTarget_Tip::Event_TipOff( void )", "COOP_CAMPAIGN_EVENT_TIP_OFF"),
    ):
        body = function_body(target, signature, "campaign HUD effect")
        require(body, event, "campaign HUD effect")


def validate_boss_bar_replication() -> None:
    game_local_h = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.h")
    for token in (
        "COOP_CAMPAIGN_EVENT_BOSS_START",
        "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_BAR",
        "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_WARN_BAR",
        "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_PERCENT",
        "COOP_CAMPAIGN_EVENT_BOSS_MAX_HEALTH",
        "COOP_CAMPAIGN_PAYLOAD_FLOAT",
        "COOP_CAMPAIGN_PAYLOAD_ENTITY",
    ):
        require(game_local_h, token, "boss bar campaign vocabulary")

    # Existing event ordinals must not move.
    if game_local_h.index("COOP_CAMPAIGN_EVENT_FADE") > game_local_h.index("COOP_CAMPAIGN_EVENT_BOSS_START"):
        raise AssertionError("boss events must be appended after the existing campaign events")
    if game_local_h.index("COOP_CAMPAIGN_PAYLOAD_FADE") > game_local_h.index("COOP_CAMPAIGN_PAYLOAD_FLOAT"):
        raise AssertionError("boss payload shapes must be appended after the existing ones")

    game_local = read(GAME_LIBS_ROOT / "src" / "game" / "Game_local.cpp")
    for signature, applier in (
        ("void idGameLocal::SendCoopCampaignFloat( int eventType, float value )",
         "ApplyCoopCampaignFloat( eventType, value );"),
        ("void idGameLocal::SendCoopCampaignEntity( int eventType, const idEntity *ent )",
         "ApplyCoopCampaignEntity( eventType, entitySpawnId );"),
    ):
        body = function_body(game_local, signature, "boss bar send path")
        require(body, "if ( isClient ) {", "boss bar send path")
        require(body, 'networkSystem->ServerSendReliableMessage( -1, outMsg );', "boss bar send path")
        if body.count(applier) != 1:
            raise AssertionError(f"{applier!r} must be applied exactly once in the send path")

    # An entity reference has to carry its spawn id, or a client resolves it to
    # whatever later entity reused the number.
    send_entity = function_body(
        game_local,
        "void idGameLocal::SendCoopCampaignEntity( int eventType, const idEntity *ent )",
        "boss identity write",
    )
    require(send_entity, "PackEntitySpawnId( spawnIds[ ent->entityNumber ], ent->entityNumber )", "boss identity write")

    network = read(GAME_LIBS_ROOT / "src" / "game" / "Game_network.cpp")
    client_read = function_body(
        network,
        "void idGameLocal::ClientProcessReliableMessage( int clientNum, const idBitMsg &msg )",
        "boss bar read",
    )
    for token in (
        "case COOP_CAMPAIGN_PAYLOAD_FLOAT:",
        "case COOP_CAMPAIGN_PAYLOAD_ENTITY:",
        "const int entitySpawnId = msg.ReadLong();",
    ):
        require(client_read, token, "boss bar read")

    # The reliable message can outrun the snapshot that spawns the boss, so the
    # reference is held rather than resolved once and dropped.
    player_h = read(GAME_LIBS_ROOT / "src" / "game" / "Player.h")
    for token in (
        "void					SetBossBattleTarget			( int entitySpawnId );",
        "void					ResolvePendingBossBattle	( void );",
        "int						pendingBossSpawnId;",
    ):
        require(player_h, token, "held boss battle reference")

    player = read(GAME_LIBS_ROOT / "src" / "game" / "Player.cpp")
    resolve = function_body(player, "void idPlayer::ResolvePendingBossBattle ( void )", "boss resolution")
    require(resolve, "gameLocal.spawnIds[ entityNum ] != ( pendingBossSpawnId >> GENTITYNUM_BITS )", "boss resolution")
    require(resolve, "StartBossBattle( enemy );", "boss resolution")

    # StartBossBattle has to clear the held id, or resolution retries forever.
    start = function_body(player, "void idPlayer::StartBossBattle ( idEntity* enemy )", "boss battle start")
    require(start, "pendingBossSpawnId = 0;", "boss battle start")

    # Resolution has to be retried, not attempted once.
    hud_stats = function_body(player, "void idPlayer::UpdateHudStats( idUserInterface *_hud )", "boss bar maintenance")
    require(hud_stats, "ResolvePendingBossBattle();", "boss bar maintenance")

    # All four boss target events reached the local player only, three of them
    # through an unchecked GetLocalPlayer()->GetHud().
    target = read(GAME_LIBS_ROOT / "src" / "game" / "Target.cpp")
    for signature, event in (
        ("void rvTarget_BossBattle::Event_Activate( idEntity *activator )", "COOP_CAMPAIGN_EVENT_BOSS_START"),
        ("void rvTarget_BossBattle::Event_AllowShieldBar( float activate )", "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_BAR"),
        ("void rvTarget_BossBattle::Event_AllowShieldWarningBar( float activate )",
         "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_WARN_BAR"),
        ("void rvTarget_BossBattle::Event_SetShieldPercent( float percent )",
         "COOP_CAMPAIGN_EVENT_BOSS_SHIELD_PERCENT"),
        ("void rvTarget_BossBattle::Event_SetBossMaxHealth( float f )", "COOP_CAMPAIGN_EVENT_BOSS_MAX_HEALTH"),
    ):
        body = function_body(target, signature, "boss bar target")
        require(body, event, "boss bar target")
        if "GetLocalPlayer()" in body:
            raise AssertionError(f"{signature} still addresses the local player only")


def main() -> int:
    if not GAME_LIBS_ROOT.is_dir():
        print(
            f"coop_game_type_routing: skipped (no openQ4-game checkout at {GAME_LIBS_ROOT})",
            file=sys.stderr,
        )
        return 0

    validate_shared_gametype_header()
    validate_module_selection()
    validate_network_command_routing()
    validate_loading_screen()
    validate_game_module_gametype()
    validate_campaign_spawns()
    validate_ai_replication()
    validate_campaign_script_replication()
    validate_boss_bar_replication()
    print("coop_game_type_routing: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
