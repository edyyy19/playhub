2. Matchmaking Overflow
   Category: PlayHub | Lobby #X (auto-numbered: PlayHub | Lobby #2, PlayHub | Lobby #2, etc.)
   Channels within the category:

-   Voice Channel: Lobby (limited to 4 players)
-   Text Channel: Lobby (displays queue status and match info)

Phase 1: Lobby Creation
Trigger: /create_lobby command
Actions:

-   Creates a new category named PlayHub | Lobby #X (auto-incremented).
-   Inside the category, creates:
    -   A voice channel named Lobby (4-player limit)
    -   A text channel named Lobby
        Embed Message Posted in Text Channel:
        LOBBY STATUS
        Players in Queue: 0/4
        Phase: Waiting for players...
        Embed Color: Yellow (#FFFF00)

Phase 2: Captain Selection & Team Drafting
Trigger: 4 players join the Lobby voice channel
Actions:

-   Randomly selects two captains from the 10 players
-   Assigns teams:
    -   Captain 1 → Counter-Terrorists (CT)
    -   Captain 2 → Terrorists (T)
        Embed Message Updates to Draft Mode:
        TEAM DRAFT
        CT Team: @Captain1
        T Team: @Captain2
        Next Pick: @Captain
        Available Players: [Dropdown Selection Menu]
        Embed Color: Blue (#3498DB)

Draft Mechanics:

-   Captains take turns selecting players (CT → T → CT → T...)
-   Each selection is made via dropdown
-   Bot updates the embed in real-time, adding selected players under each team
    Phase 3: Map Veto
    Trigger: Both teams are finalized (2v2)
    Actions:
-   Updates embed to display map veto interface:
    MAP VETO
    Maps Available: Sandstone, Rust, Province, Breeze, Sakura, Dune, Zone 7
    Next Ban: CT Captain
    Embed Color: Purple (#9B59B6)

Veto Mechanics:

-   Captains take turns banning maps via dropdown
-   Each banned map is removed from the embed dynamically
-   Final remaining map is highlighted in bold
    Phase 4: Match Finalization
    Trigger: One map remains after veto phase
    Actions:
-   Generates a unique Match ID (e.g., Match #0023)
-   Embed message updates with full match info:
    MATCH #0023
    CT Team: @Player1, @Player2
    T Team: @Player6, @Player7
    Map: Province
    Embed Color: Green (#2ECC71)
-   Add the match info into the supabase table matches

Phase 5: Team Voice Channels
Actions:

-   Creates two private voice channels:
    -   CT Voice (visible only to CT members)
    -   T Voice (visible only to T members)
-   Bot automatically moves players from the lobby voice channel to their respective team voice channels
    Phase 6: Lobby Reset and Channel Cleanup
    Trigger: Match reaches Phase 4 completion (match setup finalized)
    Actions:
-   Once Phase 4 is complete and the final embed message has been posted with match details, the lobby automatically resets to Phase 1
-   The embed in the Lobby text channel reverts to the initial queue status message:
    LOBBY STATUS
    Players in Queue: 0/4
    Phase: Waiting for players...
    Embed Color: Yellow (#FFFF00)

-   The original Lobby voice and text channels remain intact and ready for new players
    Voice Channel Cleanup Rules:
-   Temporary voice channels (CT VOICE and T VOICE) are retained for a maximum of 30 minutes
-   If no players are present in a team voice channel for 30 consecutive minutes, it will be automatically deleted
-   If players leave the voice channel earlier, it may be deleted sooner based on inactivity detection
    Error Handling & Timeouts
    Player Leaves Mid-Game:
-   If a player leaves after Phase 2 begins, they receive a 2-minute timeout
-   The lobby resets
-   Players remaining in the voice chat are not removed
-   The bot cancels the current match and reverts to the waiting phase
    Captain or Player Inactivity:
-   Each captain has 2 minutes to make a draft pick or ban a map
-   Failure to do so results in removal from the queue and a 2-minute timeout
-   Lobby resets automatically
