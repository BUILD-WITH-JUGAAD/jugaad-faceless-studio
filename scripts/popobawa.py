"""
Popobawa — one YouTube Short (up to 180 seconds).

VIDEO_TYPE (overridable with CLI model=live / model=comic / …):
  "2d_comic" / "comic"               — stills + camera motion
  "realistic_broll" / "live"         — real Pexels clips in assets/broll
  "cartoon" / "anime"                — other illustrated stills
  "ai_video"                         — paid Pollinations video
"""

VIDEO_TYPE = "live"
CHARACTER_SEED = 1995
CHARACTER_LOCK = (
    "Popobawa, a one-eyed shetani with leathery bat wings, cyclopean eye, "
    "and a haze like burning metal"
)

PARTS = [
    {
        "text": (
            "In 1995, families across Zanzibar started sleeping outside. Together. "
            "Lights on. Doors open. Not because of a storm. Because something was "
            "coming through the walls. They called it Popobawa. Bat wing. One eye. "
            "Leathery wings. A smell like burning metal before it arrives. It does "
            "not knock. It does not need to. "
            "On the island of Pemba, night turned into a watch. Parents sat in "
            "courtyards with lanterns. Children slept in piles on mats. Never alone. "
            "Never in a locked room. People said the thing hated crowds and hated "
            "light. So the island became a crowd, with the lights left on. "
            "Ali woke with bruises he could not explain. Marks across his arms, like "
            "something had pinned him down. His neighbors did not laugh. They nodded, "
            "because it had happened to them too. By morning the whole village was "
            "talking. Then the next village. Then the news. Nobody explained how a "
            "rumor could leave real bruises. "
            "Hospitals saw the panic. Police took statements. Newspapers printed the "
            "name. On Pemba, people moved mattresses into the street. Some slept in "
            "mosques. Some slept in the road with car headlights on. Radio reports "
            "spread faster than any elder's warning. If you were alone, you were prey. "
            "That was the rule for two weeks. An entire population slept outdoors "
            "rather than risk being alone behind a locked door. "
            "Here is what actually happened. The attacks always followed unrest. "
            "Elections. Land disputes. This was not a story historians dug out of a "
            "book. This was 1995. Then 2007. Then 2013. Real mass panic, recorded by "
            "real newspapers, across real islands. Every time the island shook "
            "politically, the same nights came back. Same bruises. Same smell people "
            "swore they could taste. Same decision: better the mosquitoes outside than "
            "whatever came through the wall. Some called it a demon. Some called it "
            "mass hysteria. The people who slept outside did not care what you called "
            "it. They only knew they would not lock that door and wait in the dark. "
            "Search the old headlines. Pemba. Unguja. Popobawa. The pattern is still "
            "there. When the island gets tense, the night gets worse. And if you ever "
            "visit, and the streets are full after midnight, do not assume it is a "
            "festival. They might be waiting for the smell of burning metal. They "
            "might be waiting so they do not have to sleep alone."
        ),
        "broll_query": "zanzibar night village",
        "broll_queries": [
            "zanzibar aerial coast | tanzania island coast aerial",
            "african village night lantern | rural africa night village",
            "family sleeping outdoors night | people camping village night",
            "open door night house | dark house doorway night",
            "dark bedroom night | empty bed moonlight",
            "bat flying night | bat silhouette night sky",
            "lantern courtyard night | candle lantern africa night",
            "children sleeping mats | family sleeping floor night",
            "crowded village street night | african market night crowd",
            "bruised arm close up | man holding injured arm",
            "worried villagers night | people talking village night",
            "newspaper printing press | old newspaper close up",
            "hospital corridor night | emergency hospital night",
            "election crowd africa | protest crowd night africa",
            "locked door handle night | closing bedroom door",
            "people sitting outside midnight | night street gathering africa",
        ],
        "image_prompts": [
            "Zanzibar families sleeping outside at night, lanterns on, doors open, huddled",
            "a monstrous shape coming through a bedroom wall, plaster bursting",
            "Popobawa, one-eyed bat-winged shetani filling a doorway, leathery wings",
            "Pemba courtyards full of people with lanterns, children on mats",
            "Ali waking in terror with unexplained bruises on his arms",
            "neighbors nodding grimly, same bruises, lantern-lit village street",
            "newspapers printing the name Popobawa, shocked faces",
            "an entire island sleeping outdoors for two weeks, nobody indoors",
            "political unrest in Zanzibar, elections, tense night crowds",
            "streets full after midnight, people waiting, they will not sleep alone",
        ],
    },
]
