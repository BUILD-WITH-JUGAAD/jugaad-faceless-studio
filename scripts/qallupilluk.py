"""
Qallupilluk — one YouTube Short (up to 180 seconds).
"""

VIDEO_TYPE = "live"
CHARACTER_SEED = 431
CHARACTER_LOCK = (
    "the Qallupilluk, a hag-like being in a wet moss-green amautiq, "
    "hood always ready, pale eyes, arctic sea-ice hunter"
)

PARTS = [
    {
        "text": (
            "In the Arctic, mothers warn their children never to wander too close "
            "to the sea ice alone. Not because of the cold. Because of her. The "
            "Qallupilluk waits just beneath the surface, wrapped in a wet, moss-green "
            "amautiq, the hood always ready. She does not chase. She waits until you "
            "hear her humming. "
            "The ice looks solid. It is not. Open leads hide under a skin of white. "
            "Parents say if you hear a song that sounds like someone you love, do not "
            "answer it. Do not walk toward it. That is how she hunts. "
            "A boy near Iglulik followed a song he thought was his sister. His "
            "footprints stopped at a crack in the ice. No struggle. No drag marks. "
            "Elders say when a child vanishes near open water, she has already pulled "
            "them under her hood. Some come back years later. They do not remember how. "
            "Searchers still avoid certain shorelines after dark. Too many "
            "disappearances were never explained. Dogs refuse those stretches. Lanterns "
            "go out. The humming carries over the ice like a lullaby, and then it stops. "
            "Inuit families have told this for generations. It is not a campfire joke. "
            "It is a map of tide cracks and open leads and places you do not walk after "
            "the sun drops. Mothers still grab a child's sleeve at the edge. They still "
            "say her name like a rule. "
            "This warning is older than the page it was written on. And if you ever "
            "stand at the edge, and you hear her humming, you already know the rule. "
            "Do not take another step. Turn around. Go home. Leave the ice to her."
        ),
        "broll_query": "arctic sea ice night",
        "broll_queries": [
            "arctic sea ice aerial | frozen ocean ice aerial",
            "inuit village winter night | arctic village night snow",
            "cracked sea ice close up | ice breaking ocean",
            "child walking snow dusk | child winter coat snow",
            "open water ice lead | ice hole ocean",
            "footprints in snow ice | footprints snow aerial",
            "arctic shoreline night | polar night coast",
            "searchers snow night lantern | people walking snow lantern",
            "sled dogs arctic | husky dogs snow",
            "northern lights ice | aurora over snow",
            "frozen ocean dusk | polar ice sunset",
            "empty arctic horizon | vast snow landscape",
        ],
        "image_prompts": [
            "Inuit mother stopping a child at the sea ice edge at night, lantern, urgent",
            "the Qallupilluk hag waiting under cracked ice, wet moss-green amautiq, pale eyes",
            "a child at the ice edge hearing humming from under the water",
            "a boy walking onto dusk ice, chasing a song he thinks is his sister",
            "child footprints stopping dead at a crack in the ice, overhead, no drag marks",
            "the Qallupilluk pulling a child under her hood, splash, motion",
            "a child returning years later, blank-eyed, not remembering",
            "searchers turning away from a dark arctic shoreline after nightfall",
            "dogs refusing to cross a stretch of ice, lanterns dying",
            "a child at the ice edge, would you still walk closer if you heard humming",
        ],
    },
]
