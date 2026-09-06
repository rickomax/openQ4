# Co-operative Campaign Play Plan

Date: 2026-09-06
Status: Phase 1 (routing, session, campaign-AI replication) and Phase 2 (campaign script replication, boss bars, influences and fov) in place; not yet playable end to end

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

## Phase 2a: campaign scripting

Two things stood between co-op and a campaign script running at all.

**Scripts started before anyone was there to run them against.**
`idWorldspawn::Spawn` starts a map's `main()` - and any `call` functions on
worldspawn - with `DelayedStart( 0 )`, on the frame after the map loads. In
single-player the player entity is spawned moments later by the session, so a
script that reaches for the player finds one. In co-op the engine does not spawn
players at map load at all: they arrive through `ServerClientBegin` when they
connect, and on a dedicated server that may be minutes later or never. Campaign
scripts open by reaching for the player. `QueueCoopMapScript` holds those
threads and `StartPendingCoopMapScripts` releases them, once, when the first
player spawns. `idTarget_Give`'s `onSpawn` loadout has the same problem and
re-posts itself until somebody is there to receive it.

**Script effects reached one player.** Campaign scripting was written against a
single player, so target entities and script events reach for
`GetLocalPlayer()` and act on whatever comes back. In co-op that is the listen
host and nobody else, and on a dedicated server it is `NULL` - which several of
those call sites dereferenced without checking, `rvTarget_AmmoStash` and
`idThread::Event_DrawText` among them.

Which players an effect reaches now depends on what the effect is, and there are
three answers:

- **Everyone, applied server-side.** `GetCampaignPlayers()` returns the local
  player alone outside co-op - so single-player behaviour is untouched - and
  every spawned player in co-op. `idTarget_Give` uses it: a campaign give is
  progression, not a pickup belonging to whoever walked into it.
- **Everyone, sent as a message.** Objective text, the secret-area notice, the
  tip overlay and script-driven screen fades all live in a player's own HUD or
  `playerView`, which exist only on that player's machine - the server cannot
  write to them. `GAME_RELIABLE_MESSAGE_COOP_CAMPAIGN_EVENT` carries the effect
  and each client applies it locally. The send path also applies it locally,
  because `idAsyncServer::SendReliableMessage` returns early for the local
  client and a listen host would otherwise be the one player who never sees it.
- **One player.** `GetCampaignActivator()` returns whoever triggered the effect,
  falling back to any player present. `rvTarget_AmmoStash` uses it: it spawns
  ammo into the world sized to one player's needs, so running it per player
  would spawn duplicates.

The wire format sends the payload *shape* ahead of the payload, so a receiver
reads the message by shape and only then decides whether it knows the event
type. An event type added by a newer server is ignored cleanly instead of being
misread.

Triggers themselves needed no work, which is worth recording because it is not
obvious: `idPlayer::LocalClientPredictionThink` calls
`TouchTriggers( &idItem::GetClassType() )`, filtered to items for pickup
prediction. General map triggers therefore never fire client-side, and campaign
scripting cannot double-execute.

## Phase 2b: boss bars

All five `rvTarget_BossBattle` events addressed the local player, three of them
through an unchecked `gameLocal.GetLocalPlayer()->GetHud()` - the local player
being the listen host and nobody else, or `NULL` on a dedicated server.

The boss *health* bar needed less than it looks. `idPlayer::UpdateHudStats`
already recomputes it every frame from `bossEnemy->health`, so a client whose
own `idPlayer` knows which entity the boss is draws the bar correctly with no
per-frame traffic at all - it reads the health out of the AI snapshot Phase 1
already sends. Only the boss's *identity* travels, once, as a packed spawn id so
a client cannot resolve it to a later entity that reused the number.

That identity can outrun the boss itself. Reliable messages and snapshots are
separate streams, so `COOP_CAMPAIGN_EVENT_BOSS_START` can arrive before the
snapshot that spawns the boss on that client. `idPlayer::SetBossBattleTarget`
holds the id and `ResolvePendingBossBattle` retries each frame from the same
place the bar is maintained; until it resolves, `bossEnemy` is simply unset and
the HUD is untouched, so the bar appears when the boss does. The held id is
deliberately not archived in savegames: it is only ever non-zero for the moment
between the message and the entity arriving on a co-op client, co-op clients do
not save, and archiving it would change the savegame format for no gain.

The shield bar, shield warning bar, shield fill and health-bar scale are
script-authored numbers with no entity behind them, so they travel as plain
floats.

## Phase 2c: influences and field of view

`idTarget_SetInfluence` is the largest of the script-to-player targets. Most of
what it does is world state - relighting, skins, sounds, guis - which already
replicates because those entities do. What did not travel is the half that
lives on the player: influence level, the fullscreen vision material and skin,
a forced facing, the white flash and its sound, and the field of view.
`idTarget_SetFov` is the same fov mechanism on its own.

Both were also unsafe. `idTarget_SetFov::Think`, `idTarget_SetInfluence::Think`,
`Event_Flash`, `Event_ClearFlash` and `Event_RestoreInfluence` all dereferenced
`gameLocal.GetLocalPlayer()` without checking - the `Think` cases every frame
while active - which is a crash on a dedicated co-op server.

The fov is a **curve, not a value**. Both targets drive it a frame at a time
from their own `Think`, and neither target is replicated, so on a client that
`Think` never runs. Sending a value per frame over the reliable channel would
be both wasteful and wrong. Instead the curve - start time, duration, start and
end value, and whether to clear when it finishes - is sent once, and each
player evaluates it in `idPlayer::UpdateCoopInfluenceFov`. That has to be
driven from two places, because a client's own player never reaches
`idPlayer::Think`: the server and single-player path calls it there, the client
path from `LocalClientPredictionThink`. Outside co-op the original `Think`
still drives the fov, so single-player behaviour is untouched; in co-op neither
target activates `TH_THINK` at all and every player, host included, evaluates
its own copy. This is the same shape as the boss health bar - send the driver,
not the samples.

The rest of the influence travels as one message rather than several, because
`idTarget_SetInfluence` always sets and clears those pieces together and a
receiver that applied half of it would be left in a state no script asked for.
One detail is easy to get wrong: an influence that does not ask for a vision
effect must leave whatever is on screen alone, which the original achieved by
simply not calling `SetInfluenceView`. The message carries an explicit
`setVision` flag so "do not touch it" and "clear it" stay distinguishable -
clearing an influence is the same message with `setVision` set and no material.

The flash reuses the existing fade event, and its sound travels as a string.

## Phase 3 and beyond: what is not done

Still open:

- **Script and thread state.** Scripts run server-side and their effects
  replicate, but `idThread` state itself does not. A client that joins
  mid-sequence sees the world as the snapshot describes it, not the sequence
  from its start.
- **Cinematics, level transitions, respawn, vehicles, checkpoint saves.** As
  listed under Phase 1; none of these have been started.
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
after it in the snapshot.

For Phase 2a it additionally pins that the reliable message is appended rather
than inserted, that the campaign fade is four floats and a long on both sides,
that payload shape is read before the event type is judged, that
`GetCampaignPlayers` still resolves to exactly the local player outside co-op,
that each send path applies its effect locally exactly once, that both
worldspawn script entry points defer, and that the call sites which used to
dereference `GetLocalPlayer()` unchecked no longer do.

For Phase 2b it pins that the boss events and payload shapes are appended
rather than inserted, that the boss identity is sent as a packed spawn id, that
`StartBossBattle` clears the held id (or resolution would retry forever), that
resolution is retried where the bar is maintained rather than attempted once,
and that none of the five boss target events still address the local player.

For Phase 2c it pins that the influence events and payload shapes are appended
rather than inserted, that the influence fields are written and read in the same
order, that `setVision` survives so "leave the vision alone" stays distinct from
"clear it", that the fov curve is advanced on both the server and client
per-frame paths, and that neither target still drives or dereferences the local
player.

It skips cleanly when no `openQ4-game` checkout is present.
