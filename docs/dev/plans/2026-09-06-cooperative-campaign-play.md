# Co-operative Campaign Play Plan

Date: 2026-09-06
Status: Phase 1 (routing, session and campaign-AI replication foundation) in place; not yet playable end to end

## Why this needs a plan at all

Quake 4 ships two game modules and openQ4 keeps that split: `game_sp` owns the
campaign - AI, scripting, cinematics, map content - and `game_mp` owns the
competitive match modes. Every gametype the engine knows belongs to exactly one
of them, and the engine decides which module to load before any module is
loaded, so it cannot ask the game which one wants a given `si_gameType`.

Co-op does not fit that split. It seats several real clients, so it is
multiplayer to the network code, but it plays campaign maps, so it needs
`game_sp`. Before this work the engine read "not singleplayer" as "load
`game_mp`" in four separate places, and `spawnServer`, `connect` and `reconnect`
each forced `game_mp` unconditionally. Hosting or joining anything therefore
unloaded the module that owns the campaign being played. That is the first thing
that had to change, and it is engine-side work; the gameplay half is game-side.

## The shape of the change

```
si_gameType "Coop"
  ├─ engine (openQ4)
  │    idCoopGameType::IsCoopGameTypeName()   one predicate, three call sites
  │    openQ4_SelectGameModuleBaseName()      -> game_sp, ahead of the dedicated branch
  │    Net_RequiredGameModule()               spawnServer / connect / reconnect
  │    LoadLoadingGui()                       campaign loadscreen, server info
  └─ game (openQ4-game, src/game)
       GAME_COOP                              appended to gameType_t
       idGameLocal::IsCoop()                  is this a co-op session
       idGameLocal::IsMatchGameType()         does idMultiplayerGame own the match
       InitializeSpawns / SelectSpawnPoint    campaign starts, not deathmatch starts
       idAI::WriteToSnapshot / ReadFromSnapshot   server-authoritative campaign AI
```

`GAME_COOP` is appended after `GAME_DEADZONE` rather than inserted, so every
existing gametype keeps the ordinal it already sends over the wire.

`Coop` is deliberately absent from the multiplayer route table in
`src/framework/Common.cpp`. That table mirrors `game_mp`'s own descriptor rows
and is cross-checked against them by
`tools/tests/game_type_module_selection.py`; co-op has no `game_mp` row to
mirror, and adding one would route it to the wrong module.

## Phase 1: what is in place

**Routing.** A co-op `si_gameType` selects `game_sp` on listen servers,
dedicated servers and joining clients, and `spawnServer`/`connect`/`reconnect`
keep it loaded instead of swapping to `game_mp`. The dedicated-server path is
checked before the historical "anything but singleplayer" reading, because a
dedicated co-op server is exactly the case that reading gets wrong.

**Session.** `idGameLocal::SetGameType` recognises `Coop` and returns before
`idMultiplayerGame::SetGameType`, which has no row for it and would also
overwrite `si_entityFilter` with the gametype name - campaign maps need the
filter they were built with. `idMultiplayerGame::Run` and `::Draw` are skipped
for co-op through `IsMatchGameType()`, so a co-op session runs the campaign HUD
and no match rules: no warmup, no scoring, no round flow.

**Spawning.** `InitializeSpawns` collects `info_player_start` and any authored
`info_player_coop` instead of the deathmatch and team spawns campaign maps do
not have, so the multiplayer "map must have a spawn spot" error cannot fire on a
valid campaign map. Campaign maps almost always carry exactly one start, so
`idGameLocal::FindCoopSpawnPosition` walks a widening ring around it for a
position the player fits in and can reach in a straight line from the spot
itself - nobody spawns inside a team-mate, inside geometry, or through a wall.

**Campaign AI replication.** Nothing under `src/game/ai/` implemented
`WriteToSnapshot`/`ReadFromSnapshot`, so every monster, boss and vehicle was
invisible to a networked client. `idAI` now replicates presentation state -
physics, bind, per-channel animation, facing, health, damageability, visibility -
and marks itself `networkSync` in co-op only. Clients run no AI logic: they
inherit `idAnimatedEntity::ClientPredictionThink`, which runs physics, animation
and presentation and nothing else, so a client can never disagree with the
server about what a monster is doing.

Animation is sent as the animation index each channel is playing plus its start
time, not as the anim-state machine that chose it. The start time keeps a
briefly-lagged client in phase instead of restarting every animation from frame
zero, and the index is range-checked against the receiving client's own animator
before it is played.

## Phase 2 and beyond: what is not done

Phase 1 is the foundation, not a playable campaign. Still open, roughly in
dependency order:

- **Scripted sequences.** Campaign progression is script-driven and runs
  server-side only. Triggers, objectives, door and lift scripting, and
  `idThread` state need replication or client-side re-derivation.
- **Cinematics.** `inCinematic` gates player control and camera. Co-op needs a
  policy for players who are not the cinematic's subject.
- **Level transitions.** Campaign maps chain through `EndLevel`; co-op needs all
  players moved together, with inventory carried across.
- **Player death and respawn.** The campaign has no respawn model. Co-op needs
  one - checkpoint reload, team-mate revival, or spectate-until-clear.
- **Vehicles and on-rails sequences.** Quake 4 has both; neither is replicated.
- **Save and checkpoint.** Campaign saves assume a single player.
- **Menus and server browser.** No co-op entry point exists yet; a co-op session
  is reachable only by setting `si_gameType` and using the console. A joining
  client has to carry `si_gameType Coop` into the `connect`, because the
  server's serverInfo does not arrive until after the module is chosen - the
  browser will need to set it.

## Prior art

LibreCoop, the open-source co-op mod for dhewm3, solves the same problem for
Doom 3 and is the closest reference. Its approach differs in one significant
way: it maintains a parallel entity-numbering space (`coopentities`, `coopIds`,
`coopSyncEntities`) and a second snapshot path alongside the original, because
Doom 3's entity numbering is not stable enough across client and server for
campaign content. openQ4 has not needed that so far - campaign map entities
spawn in the same order on both ends - but if entity-number drift shows up
during Phase 2, LibreCoop's parallel numbering is the proven answer.

## Validation

`tools/tests/coop_game_type_routing.py` pins the whole contract across both
repositories: the shared header, module selection ordering (including that the
co-op branch precedes the dedicated one), the three network command sites, the
loadscreen choice, the appended `GAME_COOP` ordinal, the `IsMatchGameType`
exclusions, the campaign spawn paths, and the AI snapshot field ordering -
write and read must agree, or the delta message desynchronises for every entity
after it in the snapshot. It skips cleanly when no `openQ4-game` checkout is
present.
