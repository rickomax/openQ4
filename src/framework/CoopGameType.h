/*
===========================================================================

openQ4 GPL Source Code
Copyright (C) 2026 the openQ4 contributors.

This file is part of the openQ4 Source Code. See docs/legal for details.

===========================================================================
*/

#ifndef __COOPGAMETYPE_H__
#define __COOPGAMETYPE_H__

// Co-operative play is the one mode that is multiplayer to the network code and
// single-player to the module loader: it runs campaign maps, so it needs the
// AI, scripting and content that only game_sp implements, while still seating
// several real clients.
//
// Every engine path that reads "not singleplayer" as "load game_mp" has to
// consult this instead. Without it a co-op host reloads into game_mp during
// spawnServer/connect and drops the module that owns the campaign entirely.
namespace idCoopGameType {

// si_gameType wire token. Deliberately absent from Common.cpp's multiplayer
// route table: that table mirrors game_mp's own descriptor rows, and no game_mp
// row serves co-op.
static const char * const GAMETYPE_NAME = "Coop";

// Name of the game module that serves co-op sessions.
static const char * const GAME_MODULE_NAME = "game_sp";

inline bool IsCoopGameTypeName( const char *gameType ) {
	return gameType != NULL && gameType[ 0 ] != '\0' && idStr::Icmp( gameType, GAMETYPE_NAME ) == 0;
}

// Reads the live si_gameType. Callers that already hold a serverInfo value
// should prefer IsCoopGameTypeName() against it, because serverInfo is what the
// session actually spawned with.
inline bool IsCoopGameTypeActive( void ) {
	return IsCoopGameTypeName( cvarSystem->GetCVarString( "si_gameType" ) );
}

}

#endif /* !__COOPGAMETYPE_H__ */
