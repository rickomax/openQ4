#!/usr/bin/env python3
"""Guards the client-side reliable message handlers against wire-supplied indices.

A reliable message arrives from the server, and a client has no way to know the
server is honest. Several handlers read a client number as a raw byte - so any
value 0..255 - and the per-client arrays those numbers index (userInfo among
them) are sized MAX_CLIENTS, which is 32. The entities array they were also used
to index is sized MAX_GENTITIES, 4096, so an out-of-range byte selected an
ordinary world entity rather than faulting: it was then cast to idPlayer* and
read as one, and its entityNumber indexed userInfo far past the end.

This pins the guards that close that off, in both game modules. game_sp matters
as much as game_mp now: co-op serves networked sessions from the single-player
module, so its copy of these handlers is no longer dead code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GAME_LIBS_ROOT = Path(os.environ.get("OPENQ4_GAMELIBS_REPO", ROOT.parent / "openQ4-game")).resolve()
MODULES = ("game", "mpgame")


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


def case_block(handler: str, label: str, context: str) -> str:
    start = handler.find(label)
    if start < 0:
        raise AssertionError(f"Missing {label!r} in {context}")
    end = handler.find("\n\t\tcase ", start + len(label))
    return handler[start:end] if end > 0 else handler[start:]


def case_blocks(handler: str, label: str) -> list[str]:
    blocks = []
    start = handler.find(label)
    while start >= 0:
        end = handler.find("\n\t\t\tcase ", start + len(label))
        nxt = handler.find("\n\t\tcase ", start + len(label))
        if end < 0 or (0 <= nxt < end):
            end = nxt
        blocks.append(handler[start:end] if end > 0 else handler[start:])
        start = handler.find(label, start + len(label))
    return blocks


def validate_module(module: str) -> None:
    header = read(GAME_LIBS_ROOT / "src" / module / "Game_local.h")
    network = read(GAME_LIBS_ROOT / "src" / module / "Game_network.cpp")

    require(
        header,
        "static bool				IsValidWireClientNum( int wireClientNum ) { return wireClientNum >= 0 && wireClientNum < MAX_CLIENTS; }",
        f"{module} wire client number predicate",
    )
    require(
        header,
        "idPlayer *				GetPlayerFromWireClientNum( int wireClientNum );",
        f"{module} wire client number resolver",
    )

    resolver = function_body(
        network,
        "idPlayer *idGameLocal::GetPlayerFromWireClientNum( int wireClientNum )",
        f"{module} wire client number resolver",
    )
    # Range alone is not enough: an in-range slot can still hold a non-player,
    # and a slot can be empty.
    require(resolver, "if ( !IsValidWireClientNum( wireClientNum ) ) {", f"{module} resolver range check")
    require(resolver, "ent->IsType( idPlayer::GetClassType() )", f"{module} resolver type check")
    require(resolver, "ent == NULL", f"{module} resolver null check")

    handler = function_body(
        network,
        "void idGameLocal::ClientProcessReliableMessage( int clientNum, const idBitMsg &msg )",
        f"{module} client reliable message handler",
    )

    # No handler may cast an entity chosen by the wire straight to a player.
    for pattern in (
        "static_cast<idPlayer*>(gameLocal.entities[",
        "static_cast<idPlayer *>(gameLocal.entities[",
    ):
        if pattern in handler:
            raise AssertionError(
                f"{module}: client reliable handler still casts a wire-selected entity to idPlayer*"
            )

    death = case_block(handler, "case GAME_RELIABLE_MESSAGE_DEATH:", f"{module} death message")
    require(death, "GetPlayerFromWireClientNum( attackerEntityNumber )", f"{module} death attacker")
    require(death, "GetPlayerFromWireClientNum( victimEntityNumber )", f"{module} death victim")

    vote = case_block(handler, "case GAME_RELIABLE_MESSAGE_STARTVOTE:", f"{module} vote message")
    require(vote, "if ( !IsValidWireClientNum( clientNum ) ) {", f"{module} vote caller")

    packed = case_block(handler, "case GAME_RELIABLE_MESSAGE_STARTPACKEDVOTE:", f"{module} packed vote message")
    require(packed, "if ( !IsValidWireClientNum( clientNum ) ) {", f"{module} packed vote caller")
    # A kick vote names its target too, and that is a separate wire byte.
    require(packed, "IsValidWireClientNum( voteData.m_kick )", f"{module} packed kick vote target")

    # game_mp carries two SPAWN_PLAYER labels in this function: one in the
    # repeater-inhibit switch that reads nothing, and the real handler. Every
    # block that actually reads a client number has to bound it.
    readers = 0
    for spawn in case_blocks(handler, "case GAME_RELIABLE_MESSAGE_SPAWN_PLAYER:"):
        if "int client = msg.ReadByte();" not in spawn:
            continue
        readers += 1
        if "IsValidWireClientNum( client )" not in spawn and "client >= MAX_CLIENTS" not in spawn:
            raise AssertionError(f"{module}: spawn-player accepts an out-of-range client number")
    if readers != 1:
        raise AssertionError(f"{module}: expected exactly one spawn-player reader, found {readers}")


def main() -> int:
    if not GAME_LIBS_ROOT.is_dir():
        print(
            f"client_reliable_message_bounds: skipped (no openQ4-game checkout at {GAME_LIBS_ROOT})",
            file=sys.stderr,
        )
        return 0

    for module in MODULES:
        validate_module(module)
    print("client_reliable_message_bounds: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
