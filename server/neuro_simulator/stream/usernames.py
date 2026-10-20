"""随机用户名生成器。

Twitch 真实用户名只有 `[A-Za-z0-9_]`，这里为了词面变化也允许少量连字符/空格
（观感上接近 7TV display name）。生成策略：

1. 35% 直接用 STANDALONE 里的独立用户名；其余从形容词 / 动词 / 名词三张表里
   抽两张拼接（形容词+名词最常见，动词+名词次之，形容词+动词最少）。
2. 拼接符随机：直接拼 / 下划线 / 连字符 / 空格（权重依次递减）。
3. 25% 概率追加数字后缀（420、69、987 一类）。
4. 随机做大小写处理（全小写 / 首字母大写 / 全大写）。
5. 随机做 leetspeak 替换（o→0、l→1、s→5、to→2、for→4）。

词表规模是"撞名率"的关键：四张表合计约 1800 词（standalone 492 /
形容词 447 / 动词 437 / 名词 459），组合空间十万级起步；词表来自开源英文
词表（taikuukaits/SimpleWordlists、darius/corpora、google-10000-english）
与 Twitch/BTTV/7TV 全局 emote 名单，再经格式与敏感词过滤。
chatbot 对每条弹幕都重新生成一次用户名。
"""

import random
from typing import Union

# 独立用户名：单词本身就像 Twitch 观众昵称（emote 名、动物、食物、meme 词）。
STANDALONE = [
    "pogchamp", "catjam", "gymbag", "weirdchamp", "monkas", "kekw", "lul", "kappa", "sadge", "copium",
    "peepo", "wideherd", "clap", "ohmygod", "biblethump", "kreygasm", "resident", "sleepy", "toast", "bread",
    "cookie", "goblin", "dingus", "gremlin", "anteater", "mosquito", "coldfish", "turtle", "otter", "possum",
    "raccoon", "ferret", "pigeon", "moth", "slug", "toad", "wizard", "knight", "ninja", "pirate",
    "cowboy", "viking", "samurai", "toaster", "kettle", "spoon", "fork", "ladle", "bucket", "lamp",
    "cactus", "noodle", "waffle", "nugget", "pickle", "muffin", "biscuit", "pretzel", "bubble", "sprout",
    "pebble", "acorn", "puddle", "gizmo", "widget", "sprocket", "lilz", "mcat", "zlil", "anele",
    "ayaya", "dxcat", "glorp", "hahaa", "kapow", "keepo", "kippa", "kkona", "reckh", "smorc",
    "sqshy", "wowee", "bcouch", "catbag", "dududu", "funrun", "mvgame", "nomnom", "notatk", "opieop",
    "osfrog", "petpet", "pjsalt", "ssssss", "ttours", "unsane", "vohiyo", "wtruck", "youwhy", "brobalt",
    "budstar", "burself", "coolcat", "dbstyle", "dogface", "duckerz", "dxabomb", "giveplz", "heyguys", "ohmydog",
    "ossloth", "pepepls", "pjsugar", "pmstwin", "prchase", "primeme", "redcoat", "sobayed", "sosgame", "sourpls",
    "takenrg", "taxibro", "teatime", "votenay", "voteyea", "wutface", "acestare", "babyrage", "batchest", "bttvnice",
    "budblast", "cmonbruh", "copythis", "curselit", "daesuppy", "datsauce", "dogchamp", "eagleeye", "failfish", "frankerz",
    "imglitch", "jebaited", "nonospot", "nymncorn", "oskomodo", "panicvis", "peepopls", "peeposad", "pipehype", "poledoge",
    "poooound", "praiseit", "raintime", "rarepepe", "ritzmitz", "rulefive", "thetarfu", "thething", "tinyface", "unclenox",
    "winetime", "bcwarrior", "blargnaut", "brainslug", "corgiderp", "elegiggle", "firespeed", "fishmoley", "fungineer", "futureman",
    "hailhelix", "hotpokket", "jkanstyle", "kappacool", "nanaayaya", "omgscoots", "partytime", "pastathat", "permasmug", "pianotime",
    "picomause", "saltycorn", "seemsgood", "shadylulu", "smoocherz", "swiftrage", "theringer", "thunbeast", "watchusay", "aliendance",
    "angelthump", "bigbrother", "bloodtrail", "crayontime", "guitartime", "humblelife", "kappaclaus", "manchicken", "ninjatroll", "notsquishy",
    "peepohappy", "petezaroll", "punchtrees", "raccattack", "rareparrot", "roxypotato", "strawbeary", "tbtacoleft", "tehpolecat", "twitchraid",
    "waytoodank", "wholewheat", "bleedpurple", "concerndoge", "feelsbadman", "fpsmarksman", "gingerpower", "grammarking", "kappawealth", "latersooner",
    "notlikethis", "panicbasket", "partyparrot", "shazbotstix", "soonerlater", "supervinlin", "tbtacoright", "uleetbackup", "coolstorybob", "deilluminati",
    "feelsdankman", "feelsgoodman", "feelsokayman", "feelssnowman", "foreveralone", "peopleschamp", "ripepperonis", "shoopdawhoop", "stinkycheese", "tbcheesepull",
    "beaniehipster", "feelssnowyman", "feelsweirdman", "mrdestructoid", "optimizeprime", "petezarolltie", "feelsstrongman", "freakinstinkin", "nerfredblaster", "stonelightning",
    "mlg", "mvp", "oof", "sus", "derp", "lulz", "noob", "peel", "sock", "taco",
    "bagel", "chibi", "floof", "funky", "gnome", "mochi", "noobs", "otaku", "ramen", "snacc",
    "sussy", "waifu", "yikes", "amogus", "bussin", "chonky", "coping", "cringe", "hopium", "kawaii",
    "lurker", "mining", "mitten", "pepega", "pillow", "senpai", "sweats", "bedhead", "blanket", "brownie",
    "burrito", "camping", "farming", "fishing", "griefer", "hammock", "malding", "noodles", "nuggets", "pancake",
    "pickles", "poggers", "pudding", "tendies", "tryhard", "weeaboo", "yandere", "bigbrain", "dumpling", "grinding",
    "huggable", "husbando", "lemonade", "seething", "tsundere", "braincell", "emptybrain", "tryharding", "unkillable", "couchpotato",
    "galaxybrain", "respawnable", "smoothbrain", "speedrunner", "ape", "cap", "cow", "dog", "elf", "elk",
    "epi", "ewe", "fig", "fun", "gnu", "hag", "hog", "imp", "jar", "mop",
    "nix", "nut", "oni", "orc", "pea", "rat", "roc", "rug", "rye", "yak",
    "yam", "beef", "beet", "belt", "boar", "bowl", "bull", "card", "cars", "case",
    "cork", "corn", "crow", "date", "deer", "dill", "dove", "dude", "faun", "fish",
    "flag", "ibex", "kale", "keys", "kilt", "lich", "lime", "lion", "lynx", "milk",
    "mink", "mole", "mule", "naan", "naga", "nail", "newt", "ogre", "okra", "oryx",
    "pail", "pear", "pile", "pita", "plum", "puma", "ring", "robe", "rope", "roti",
    "sage", "soap", "sofa", "suit", "taro", "tray", "tree", "tube", "vase", "vest",
    "warg", "whip", "wolf", "word", "yeti", "anise", "apple", "baked", "basil", "bison",
    "boots", "boule", "bravo", "camel", "chain", "chair", "chalk", "chard", "chest", "clock",
    "couch", "demon", "dingo", "djinn", "dryad", "dwarf", "expat", "finch", "flask", "floor",
    "genie", "ghast", "ghost", "ghoul", "glass", "golem", "guava", "harpy", "hoody", "horse",
    "hydra", "hyena", "jeans", "knife", "koala", "kurta", "lemur", "light", "llama", "mamey",
    "mango", "moose", "mummy", "novel", "nymph", "olive", "onion", "panda", "pants", "paper",
    "peach", "pixie", "plate", "pouch", "purse", "quilt", "rhino", "rowdy", "ruler", "satyr",
    "scarf", "scone", "screw", "shark", "shawl", "sheep", "shirt", "shrew", "siren", "skirt",
    "skunk", "sloth",
]

ADJECTIVES = [
    "pog", "kappa", "based", "sigma", "frozen", "toxic", "soggy", "elite", "certified", "local",
    "angry", "tiny", "mega", "hyper", "lurking", "vip", "modless", "tired", "drip", "fungal",
    "cosmic", "rat", "bug", "gamer", "chess", "anteater", "cookie", "hype", "quiet", "sleepy",
    "feral", "cursed", "blessed", "chaotic", "smug", "clueless", "salty", "sweaty", "spicy", "crunchy",
    "greasy", "haunted", "moist", "fuzzy", "slippery", "suspicious", "unhinged", "polite", "rude", "lost",
    "confused", "hungry", "thirsty", "noisy", "silent", "lazy", "eager", "brave", "cowardly", "dramatic",
    "glitched", "laggy", "buffering", "offline", "online", "anonymous", "random", "generic", "premium", "budget",
    "discount", "illegal", "legal", "unpaid", "selfmade", "caffeinated", "dehydrated", "ago", "apt", "bad",
    "dim", "far", "fly", "hot", "lay", "low", "mad", "new", "odd", "old",
    "pet", "red", "sad", "aft", "bum", "coy", "dud", "fab", "hex", "icy",
    "kin", "lax", "mum", "rum", "shy", "sly", "wry", "dank", "nosy", "able",
    "aged", "anti", "away", "bald", "beat", "bent", "best", "blue", "bold", "bone",
    "born", "bush", "busy", "cold", "cute", "dark", "dead", "dear", "deep", "dual",
    "east", "easy", "epic", "eyed", "fair", "fake", "firm", "flip", "foul", "full",
    "game", "glad", "gone", "good", "grey", "hard", "held", "high", "home", "huge",
    "hurt", "last", "late", "less", "like", "live", "long", "loud", "made", "male",
    "mass", "mere", "mild", "mint", "near", "next", "nice", "nuts", "okay", "oval",
    "paid", "pale", "poor", "port", "pure", "real", "rear", "rich", "rose", "ruby",
    "safe", "said", "self", "sent", "shed", "shut", "sick", "side", "size", "slim",
    "slow", "sold", "sole", "solo", "sure", "tall", "thin", "tops", "true", "twin",
    "ugly", "vast", "void", "warm", "well", "wide", "wild", "worn", "zero", "weird",
    "derpy", "dorky", "dusty", "gooey", "goofy", "gusty", "hefty", "janky", "musty", "rusty",
    "vibes", "wonky", "woozy", "acute", "aging", "alert", "alive", "alpha", "armed", "awful",
    "black", "blame", "blond", "bound", "broke", "built", "chief", "civil", "comic", "crazy",
    "dated", "dirty", "drawn", "drunk", "early", "elect", "ended", "extra", "false", "fifty",
    "fired", "fixed", "fluid", "focal", "front", "funny", "giant", "grand", "green", "grown",
    "hairy", "heard", "hired", "ideal", "known", "later", "liked", "liver", "loose", "lucky",
    "macro", "merry", "micro", "mixed", "moral", "moved", "nasty", "noble", "noted", "older",
    "outer", "piano", "plane", "prior", "proof", "quick", "radio", "retro", "rough", "royal",
    "rural", "saved", "sharp", "short", "small", "solid", "spent", "stock", "stuck", "super",
    "swift", "thick", "tight", "token", "tough", "tried", "union", "upper", "urban", "valid",
    "vital", "white", "wired", "worst", "wound", "young", "bouncy", "cheesy", "chunky", "crusty",
    "drowsy", "hoarse", "queasy", "sneaky", "social", "sticky", "wobbly", "glitchy", "jittery", "abuzz",
    "afire", "ahead", "alone", "antic", "arid", "atrip", "aware", "axial", "bandy", "bated",
    "beefy", "bitty", "bluff", "bonny", "boss", "brag", "brown", "bumpy", "campy", "civic",
    "cool", "crisp", "cushy", "dazed", "dewy", "dizzy", "dopy", "dread", "faced", "fated",
    "final", "flaky", "foamy", "free", "furry", "gaunt", "godly", "grim", "gutsy", "happy",
    "hazel", "hind", "holy", "husky", "inert", "jade", "jolly", "kempt", "lacy", "leafy",
    "limp", "loco", "lossy", "lumpy", "lyric", "meaty", "milky", "mirky", "moony", "mousy",
    "mural", "naive", "naval", "nifty", "north", "nutty", "olden", "overt", "past", "pedal",
    "pesky", "pilar", "polar", "prim", "proud", "puny", "rabid", "ratty", "riled", "roast",
    "rosy", "runic", "rutty", "sassy", "seamy", "shady", "showy", "slack", "slimy", "snub",
    "soft", "soupy", "splay", "squat", "stern", "straw", "sulky", "swell", "taboo", "tangy",
    "tawny", "tepid", "tidy", "tinny", "tonic", "trite", "ultra", "unlit", "utter", "vocal",
    "waxed", "weedy", "wily", "wispy", "worse", "yucky", "zonal",
]

VERBS = [
    "watch", "cry", "eat", "sleep", "lag", "spin", "yeet", "vibe", "cook", "burn",
    "chill", "chase", "bite", "bark", "meow", "scream", "yell", "whisper", "spam", "lurk",
    "post", "type", "click", "scroll", "refresh", "wait", "run", "hide", "jump", "fall",
    "float", "drift", "glitch", "crash", "load", "save", "delete", "copy", "paste", "mash",
    "hold", "drop", "throw", "catch", "lick", "sniff", "stare", "blink", "judge", "argue",
    "agree", "disagree", "forget", "remember", "dream", "panic", "vanish", "return", "haunt", "beg",
    "steal", "trade", "bet", "grind", "queue", "ping", "mute", "ban", "respawn", "int",
    "mod", "add", "ask", "bat", "bow", "dam", "dry", "end", "fax", "fix",
    "fry", "hop", "hum", "jam", "jog", "log", "man", "mix", "mug", "owe",
    "pat", "pop", "rob", "rot", "rub", "sin", "sip", "ski", "tap", "tip",
    "tow", "try", "use", "zip", "kick", "play", "talk", "buff", "camp", "clap",
    "cope", "flex", "heal", "mald", "mine", "nerf", "pull", "roll", "slay", "tank",
    "tilt", "walk", "back", "bang", "beam", "boil", "book", "bump", "bury", "call",
    "chew", "clip", "comb", "curl", "drag", "drip", "drum", "earn", "fade", "fear",
    "film", "fire", "flow", "fool", "gaze", "glue", "grin", "hand", "harm", "hate",
    "heap", "help", "hook", "hunt", "join", "kill", "knit", "land", "list", "look",
    "make", "mate", "mend", "miss", "moor", "name", "nest", "obey", "pack", "part",
    "peck", "peep", "plan", "poke", "pray", "push", "rain", "rely", "rock", "rush",
    "sail", "seal", "shop", "sigh", "skip", "slip", "soak", "stay", "stir", "suck",
    "sync", "test", "tick", "tour", "trip", "turn", "wail", "want", "warn", "wave",
    "wipe", "work", "yawn", "zoom", "block", "break", "build", "carry", "combo", "craft",
    "dance", "dodge", "gacha", "laugh", "parry", "place", "punch", "smile", "snipe", "spawn",
    "admit", "align", "allow", "annoy", "apply", "avoid", "bathe", "blind", "boast", "brake",
    "brush", "cache", "cause", "check", "cheer", "claim", "clean", "close", "coach", "cough",
    "cover", "crack", "crawl", "cross", "curve", "cycle", "delay", "doubt", "dress", "email",
    "embed", "enjoy", "enter", "fancy", "fence", "flash", "force", "found", "grate", "greet",
    "groan", "guess", "guide", "hover", "hurry", "kneel", "knock", "learn", "level", "marry",
    "match", "mourn", "occur", "order", "paint", "pause", "phone", "pinch", "point", "press",
    "prick", "query", "reach", "reign", "reply", "rhyme", "scare", "scold", "scrub", "serve",
    "share", "shave", "shrug", "smash", "smoke", "sound", "spare", "speed", "spell", "spoil",
    "spray", "stage", "stamp", "steer", "store", "strip", "stuff", "tease", "tempt", "touch",
    "trace", "train", "treat", "trust", "twist", "visit", "waste", "weigh", "whine", "worry",
    "wreck", "clutch", "crouch", "juggle", "reboot", "rejoin", "reroll", "seethe", "summon", "accept",
    "admire", "afford", "appear", "arrest", "assume", "attack", "battle", "belong", "borrow", "branch",
    "bubble", "charge", "commit", "damage", "define", "depend", "detect", "double", "enable", "ensure",
    "excite", "expand", "extend", "filter", "follow", "gather", "hammer", "happen", "ignore", "inform",
    "injure", "intend", "invert", "launch", "manage", "meddle", "muddle", "notice", "object", "offend",
    "permit", "polish", "prefer", "record", "refuse", "reject", "remind", "rename", "repeat", "rescue",
    "retire", "reveal", "reword", "search", "shiver", "snatch", "soothe", "squash", "squeal", "stroke",
    "supply", "target", "travel", "unlock", "untidy", "upload", "wander", "wonder", "counter", "flexing",
    "forfeit", "lagging", "rematch", "retreat", "sniping", "stealth", "timeout", "analyse", "attract", "comment",
    "confuse", "deceive", "develop", "display", "exclude", "impress", "measure", "present", "promise", "realise",
    "release", "restore", "scratch", "succeed", "suspend", "upgrade", "wriggle", "backseat", "ragequit", "spamming",
    "speedrun", "annotate", "describe", "instruct", "preserve", "scribble", "validate",
]

NOUNS = [
    "fan", "enjoyer", "lord", "king", "queen", "bot", "guy", "dingus", "cop", "wizard",
    "gremlin", "simp", "main", "lurker", "mod", "trash", "bug", "ant", "bird", "ninja",
    "knight", "goblin", "demon", "angel", "chad", "expert", "cat", "dog", "frog", "duck",
    "goose", "moth", "worm", "snail", "crab", "toast", "bread", "noodle", "waffle", "pickle",
    "bean", "potato", "onion", "chair", "couch", "lamp", "spoon", "bucket", "door", "window",
    "fridge", "pixel", "cursor", "packet", "server", "router", "cable", "keyboard", "gamer", "streamer",
    "viewer", "clown", "pirate", "cowboy", "ghost", "skeleton", "zombie", "vampire", "gnome", "troll",
    "ogre", "egg", "ace", "age", "aim", "amp", "arm", "art", "bag", "ban",
    "bay", "bed", "bet", "box", "bus", "cab", "cam", "car", "cod", "cup",
    "dot", "era", "eve", "fee", "flu", "fog", "fox", "fur", "gap", "gel",
    "gem", "gym", "hat", "hip", "ice", "inn", "ion", "jay", "jet", "joy",
    "key", "kit", "lab", "lat", "law", "leg", "lid", "lit", "map", "mat",
    "min", "mud", "net", "oak", "oil", "pad", "pan", "pen", "pie", "pig",
    "pit", "pod", "pro", "pub", "ram", "rap", "raw", "ray", "rim", "rod",
    "row", "saw", "sky", "spa", "spy", "sum", "sun", "tan", "tar", "tea",
    "tie", "tin", "ton", "top", "tub", "vat", "war", "way", "web", "wet",
    "wit", "wow", "zoo", "soup", "acid", "arch", "atom", "band", "base", "bear",
    "bell", "bias", "boat", "bomb", "boot", "bulk", "buzz", "calm", "cart", "cave",
    "chef", "clan", "coat", "cook", "core", "cost", "cube", "dare", "dawn", "deal",
    "deck", "desk", "dirt", "dive", "dust", "echo", "even", "eyes", "fall", "fast",
    "feel", "fine", "flux", "food", "fort", "fuel", "gage", "gear", "give", "goat",
    "grab", "grid", "hack", "halo", "heat", "hide", "hire", "hope", "hour", "idle",
    "iron", "java", "junk", "kind", "lace", "lamb", "lawn", "lean", "life", "line",
    "logo", "lost", "lung", "mark", "math", "memo", "mile", "mini", "mood", "navy",
    "news", "noon", "odds", "oven", "pair", "pass", "pick", "pink", "plus", "poll",
    "pork", "punk", "raid", "read", "reef", "rice", "rise", "root", "rule", "salt",
    "seat", "sell", "show", "site", "snap", "soil", "soul", "stem", "surf", "take",
    "task", "teen", "term", "tile", "tone", "trap", "trim", "tune", "vice", "wage",
    "ward", "ways", "west", "wine", "wish", "year", "zone", "mouse", "actor", "alien",
    "arbor", "asset", "baker", "batch", "belly", "birth", "blend", "bones", "brand", "brief",
    "bunch", "cargo", "charm", "chick", "class", "clone", "color", "count", "creek", "daily",
    "decor", "diary", "draft", "drove", "elder", "entry", "fairy", "ferry", "fight", "flesh",
    "flyer", "fruit", "genre", "globe", "grain", "grave", "grove", "handy", "honey", "house",
    "input", "joint", "label", "layer", "lemon", "lobby", "lover", "major", "march", "mercy",
    "minor", "money", "mouth", "nerve", "nylon", "omega", "panel", "party", "pearl", "pitch",
    "plant", "power", "promo", "quest", "radar", "rapid", "relay", "right", "rooms", "route",
    "sales", "scene", "sense", "shake", "sheet", "shoes", "skill", "smart", "south", "spots",
    "start", "steps", "stops", "sugar", "swing", "teach", "theme", "times", "tooth", "track",
    "trial", "truth", "unity", "value", "virus", "wagon", "wheat", "wings", "world", "wrist",
    "youth", "orange", "alpaca", "banana", "blotto", "buckle", "canary", "canvas", "cellar", "cliche",
    "cravat", "daikon", "dragon", "durian", "feijoa", "garage", "gloves", "gorgon", "hollow", "iguana",
    "jaguar", "jujube", "kevlar", "lavash", "locket", "lychee", "matzoh", "movies", "papaya", "poncho",
    "quince", "remote", "sandal", "shorts", "sphinx", "sprite", "tights", "trivet", "turnip", "walrus",
    "wrench", "aardvark", "awe", "breeds", "coins", "database", "dishes", "economy", "exterior", "flame",
    "fury", "grant", "idiot", "jock", "lavender", "noise", "package", "pets", "ports", "pun",
    "reek", "robot", "scorn", "still", "thread", "triangle", "verge", "weights", "zucchini",
]

# 数字后缀：空串占大头（真实用户名多数不带数字）
_SUFFIXES = [
    "", "", "", "", "", "", "", "", "", "",
    "420", "69", "21", "7", "99", "987", "1000", "01", "2", "3", "13", "666",
]

# 拼接符：直接拼与下划线是 Twitch 常态，连字符/空格只占少量
_SEPARATORS = ["", "", "", "_", "_", "_", "-", " "]

_LEET_RULES = [("o", "0"), ("l", "1"), ("s", "5"), ("to", "2"), ("for", "4")]


def _apply_case(name: str, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.58:
        return name
    if roll < 0.84:
        return name[:1].upper() + name[1:]
    return name.upper()


def _apply_leet(name: str, rng: random.Random) -> str:
    """随机 0~2 处 leetspeak 替换；目标字符不存在就换一条规则。"""
    hits = rng.choices((0, 1, 2), weights=(62, 30, 8))[0]
    rules = list(_LEET_RULES)
    rng.shuffle(rules)
    for src, dst in rules:
        if hits <= 0:
            break
        if src not in name:
            continue
        # 多字符规则（to/for）只替换首次出现，避免整词变形
        name = name.replace(src, dst, 1 if len(src) > 1 else -1)
        hits -= 1
    return name


def random_username(seed: Union[int, str, None] = None) -> str:
    """生成一个随机观众用户名。

    seed 非 None 时使用独立的 Random 实例：同样的种子得到同样的名字。
    chatbot 用 `(纳秒时间戳, 消息内容)` 做种子——每条消息的随机性互相独立，
    也不依赖进程内共享的 RNG 状态（并发任务之间不会有隐式耦合）。
    名字不保证唯一：真实直播间一个观众连发多条、偶尔撞名都是正常的。
    """
    rng = random.Random(seed) if seed is not None else random
    if rng.random() < 0.35:
        base = rng.choice(STANDALONE)
    else:
        pair = rng.choices(("an", "vn", "av"), weights=(50, 30, 20))[0]
        if pair == "an":
            first, second = rng.choice(ADJECTIVES), rng.choice(NOUNS)
        elif pair == "vn":
            first, second = rng.choice(VERBS), rng.choice(NOUNS)
        else:
            first, second = rng.choice(ADJECTIVES), rng.choice(VERBS)
        base = first + rng.choice(_SEPARATORS) + second
    base += rng.choice(_SUFFIXES)
    return _apply_case(_apply_leet(base, rng), rng)
